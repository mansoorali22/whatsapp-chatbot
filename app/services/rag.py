import re
from sqlalchemy.orm import Session
from sqlalchemy import desc
from sqlalchemy.exc import OperationalError

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser

from app.core.config import settings
from app.db.models import ChatLog

# Welcome message for new users (first message only)
WELCOME_INTRO_NL = (
    "Ik beantwoord graag al je vragen over sportvoeding, herstel, gezonde voeding en recept inspiratie. "
    "Verwacht praktische tips, evidence-based advies en ideeën die je meteen kunt toepassen in je keuken en sport voorbereiding!"
)
WELCOME_INTRO_EN = (
    "I'm happy to answer your questions about sports nutrition, recovery, healthy eating and recipe inspiration. "
    "Expect practical tips, evidence-based advice and ideas you can use straight away in your kitchen and training."
)

# Out-of-context reply (language-aware: NL, EN, or both only when user mixes languages)
REFUSAL_MESSAGE_NL = "Helaas kan ik je bij deze vraag niet helpen. Wel help ik je graag verder met vragen over sportvoeding!"
REFUSAL_MESSAGE_EN = "Unfortunately, I can't help you with this question. However, I'm happy to help with questions about sports nutrition!"
REFUSAL_MESSAGE = (
    REFUSAL_MESSAGE_EN + "\n\n" + REFUSAL_MESSAGE_NL
)  # fallback bilingual


def _strip_refusal_from_answer(answer: str) -> str:
    """Remove refusal phrases and 'page number not in index' if the model wrongly added them."""
    if not answer or len(answer.strip()) < 20:
        return answer
    # Refusal phrases that must not appear after we have relevant excerpts
    refusal_tails = [
        "\n\nI don't know. This is outside the book's context.",
        "\nI don't know. This is outside the book's context.",
        "\n\nIk weet het niet. Dit is buiten de context van het boek.",
        "\nIk weet het niet. Dit is buiten de context van het boek.",
        "\n\nUnfortunately, I can't help you with this question.",
        "\n\nHelaas kan ik je bij deze vraag niet helpen.",
    ]
    out = answer
    for tail in refusal_tails:
        if tail in out:
            out = out.replace(tail, "").strip()
    for old in ("I don't know. This is outside the book's context.", "Ik weet het niet. Dit is buiten de context van het boek."):
        if out.endswith(old):
            out = out[: -len(old)].strip()
            break
    # Remove "page number not in index" / "pagina nummer niet in index" (never show to user)
    for bad in (
        "(zie pagina nummer niet in index)",
        "(page number not in index)",
        "pagina nummer niet in index",
        " page number not in index",
    ):
        if bad in out:
            out = out.replace(bad, "").replace("  ", " ").strip()
            if out.endswith("()."):
                out = out[:-3].strip()
            elif out.endswith("()"):
                out = out[:-2].strip()
    return out if out else answer


def _message_suggests_dutch(user_message: str) -> bool:
    """True if the user's message content suggests Dutch (for citations only; no DEFAULT_LANGUAGE)."""
    if not user_message or not user_message.strip():
        return False
    msg = user_message.lower().strip()
    dutch_cues = [
        "pagina", "welke", "waar", "bladzijde", "vind", "staat", "recept", "het boek",
        "een vraag", "van de", "op welke", "welke pagina", "kunt u", "kun je",
        "graag", "alsjeblieft", "dank", "bedankt", "hoeveel", "waarom", "wanneer",
        "hoi", "halloo", "hallo", "hoe ", "hoe werkt", "wie ben", "vertellen", "mij ",
        "jij ", "dit ", "werkt", "gedaan", "dankjewel", "dank je",
        "goedemorgen", "goedemiddag", "goedenmiddag", "goedenavond", "goedendag",
        "goede morgen", "goede middag", "goede avond", "goede dag", "dag ", "dag!",
    ]
    english_cues = [
        "what", "which", "where", "how", "when", "why", "best", "before", "training",
        "eat", "tell me", "thank", "thanks", "please", "hello", "hi ", "hey",
        "give", "reference", "references", "source", "page",
    ]
    has_dutch = any(c in msg for c in dutch_cues)
    has_english = any(c in msg for c in english_cues)
    return has_dutch and not has_english


def _use_dutch_page_word(user_message: str) -> bool:
    """True if we should use Dutch for greeting/refusal/welcome (message or DEFAULT_LANGUAGE)."""
    if not user_message or not user_message.strip():
        lang = getattr(settings, "DEFAULT_LANGUAGE", "") or ""
        return "dutch" in lang.lower() or lang.lower() == "nl"
    lang = getattr(settings, "DEFAULT_LANGUAGE", "") or ""
    if "dutch" in lang.lower() or lang.lower() == "nl":
        return True
    msg = user_message.lower().strip()
    dutch_cues = [
        "pagina", "welke", "waar", "bladzijde", "vind", "staat", "recept", "het boek",
        "een vraag", "van de", "op welke", "welke pagina", "kunt u", "kun je",
        "graag", "alsjeblieft", "dank", "bedankt", "hoeveel", "waarom", "wanneer",
        "hoi", "halloo", "hallo", "hoe ", "hoe werkt", "wie ben", "vertellen", "mij ",
        "jij ", "dit ", "werkt", "gedaan", "dankjewel", "dank je",
        "goedemorgen", "goedemiddag", "goedenmiddag", "goedenavond", "goedendag",
        "goede morgen", "goede middag", "goede avond", "goede dag", "dag ", "dag!",
    ]
    return any(c in msg for c in dutch_cues)


def _has_english_cues(user_message: str) -> bool:
    """True if the message clearly contains English (for welcome/refusal language choice)."""
    if not user_message or not user_message.strip():
        return False
    msg = user_message.lower().strip()
    english_cues = [
        "hello", "hi ", "hey", "what", "which", "where", "how", "when", "why",
        "can you", "could you", "tell me", "common", "mistake", "athletes", "training",
        "thank", "thanks", "please", "help", "book", "nutrition", "recipe", "recipes",
    ]
    return any(c in msg for c in english_cues)


def _refusal_for_language(user_input: str) -> str:
    """Return refusal message in Dutch, English, or both only when user mixes languages."""
    dutch = _use_dutch_page_word(user_input or "")
    english = _has_english_cues(user_input or "")
    if dutch and not english:
        return REFUSAL_MESSAGE_NL
    if english and not dutch:
        return REFUSAL_MESSAGE_EN
    return REFUSAL_MESSAGE_EN + "\n\n" + REFUSAL_MESSAGE_NL


def _user_asks_for_reference(user_message: str) -> bool:
    """True if the user is asking for a reference, source, or page number (EN or NL)."""
    if not user_message or not user_message.strip():
        return False
    msg = user_message.lower().strip()
    cues = [
        "reference", "referentie", "referenties", "source", "bron", "cite",
        "page", "pagina", "bladzijde", "which page", "welke pagina", "welke bladzijde",
        "where to find", "waar vind ik", "where can i find", "give me the page",
        "with reference", "with source", "met bron", "met referentie",
        "include reference", "include source", "give reference", "in the book",
    ]
    return any(c in msg for c in cues)


def _format_references_line(used_docs: list, use_dutch: bool) -> str:
    """Build a single references line from excerpt metadata (page or section)."""
    if not used_docs:
        return ""
    seen = set()
    parts = []
    for meta in used_docs:
        page = meta.get("page")
        chunk = meta.get("chunk_index", "?")
        if page is not None and str(page).strip() and str(page) != "N/A":
            key = ("p", page)
            if key not in seen:
                seen.add(key)
                if use_dutch:
                    parts.append(f"pagina {page}")
                else:
                    parts.append(f"page {page}")
        else:
            key = ("s", chunk)
            if key not in seen:
                seen.add(key)
                if use_dutch:
                    parts.append(f"sectie {chunk}")
                else:
                    parts.append(f"section {chunk}")
    if not parts:
        return ""
    if use_dutch:
        return "Referenties: " + ", ".join(parts)
    return "References: " + ", ".join(parts)


def _answer_has_page_reference(answer: str) -> bool:
    """True if answer already contains a page/pagina number (e.g. page 42 or pagina 42)."""
    if not answer:
        return False
    return bool(re.search(r"\b(?:page|pagina)\s+\d+", answer, re.IGNORECASE))


def _localize_page_citations(user_message: str, answer: str) -> str:
    """Match citation language to user message: Dutch -> 'pagina'; English -> 'page'. Fix model output if it used the wrong language."""
    if not answer:
        return answer
    if _message_suggests_dutch(user_message):
        # User wrote in Dutch: use "pagina"
        answer = re.sub(r"\bpage\s+(\d+)", r"pagina \1", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bpages\s+", "pagina's ", answer, flags=re.IGNORECASE)
    else:
        # User wrote in English: ensure citations are in English (model sometimes outputs "Zie pagina")
        answer = re.sub(r"\bZie pagina\b", "See page", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bReferenties:\s*pagina\b", "References: page", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bpagina\s+(\d+)", r"page \1", answer, flags=re.IGNORECASE)
    return answer


def _prepend_welcome_if_first(reply: str, is_first_message: bool, user_input: str = "") -> str:
    """
    Prepend welcome intro only on the user's first message.
    Language: Dutch only, English only, or both only when the user's message mixes both.
    """
    if not is_first_message:
        return reply
    if not reply:
        reply = WELCOME_INTRO_NL
    dutch = _use_dutch_page_word(user_input or "")
    english = _has_english_cues(user_input or "")
    if dutch and not english:
        intro = WELCOME_INTRO_NL
    elif english and not dutch:
        intro = WELCOME_INTRO_EN
    else:
        intro = WELCOME_INTRO_EN + "\n\n" + WELCOME_INTRO_NL
    return intro + "\n\n" + reply


def _is_refusal_response(answer: str) -> str:
    """Return 'refused' only if the answer is essentially the refusal (no substantive content)."""
    a = answer.strip()
    if not a:
        return "refused"
    # If it's mostly the refusal message, count as refused
    if "Unfortunately, I can't help" in a and "Helaas kan ik je" in a:
        if len(a) < 280:  # roughly the length of the bilingual refusal
            return "refused"
    if "Helaas kan ik je bij deze vraag niet helpen" in a and len(a) < 200:
        return "refused"
    if "Unfortunately, I can't help you with this question" in a and len(a) < 200:
        return "refused"
    # If answer contains refusal but also substantive content (e.g. section 13, page 5), treat as answered
    if "chunk" in a.lower() or "excerpt" in a.lower() or "page" in a.lower() or "section" in a.lower():
        return "answered"
    if "I don't know" in a or "Ik weet het niet" in a or "buiten de context" in a.lower():
        return "refused"
    return "answered"


# -----------------------------
# GLOBALS
# -----------------------------
llm = None
retriever = None
intent_chain = None
split_questions_chain = None
rewrite_chain = None
answer_chain = None


# -----------------------------
# INIT RAG COMPONENTS
# -----------------------------
def init_rag_components():
    global llm, retriever, intent_chain, split_questions_chain, rewrite_chain, answer_chain

    embeddings = OpenAIEmbeddings(
        model=settings.OPENAI_EMBEDDING_MODEL
    )

    llm = ChatOpenAI(
        model=settings.OPENAI_MODEL,
        temperature=settings.OPENAI_TEMPERATURE,
        max_tokens=settings.MAX_TOKENS_RESPONSE
    )

    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name="book_chunks",
        connection=settings.DATABASE_URL,
        use_jsonb=True,
    )

    retriever = vectorstore

    # 1. Intent detection
    intent_chain = (
        ChatPromptTemplate.from_messages([
            ("system", "Classify the user message. Return ONLY one word: GREETING, THANKS, or QUESTION. "
             "GREETING: hello, hi, hey, who are you, how does this work, hoe werkt dit, wie ben jij, "
             "hallo, hoi, goedemorgen, goedemiddag, goedenavond, goedendag, dag (when used as greeting). "
             "THANKS: thanks, thank you, bedankt, dank je, dankjewel (not GREETING). "
             "Anything else that is a real question = QUESTION."),
            ("human", "{input}")
        ])
        | llm
        | StrOutputParser()
    )

    # 2. Split multiple questions (one message can contain several questions)
    split_questions_chain = (
        ChatPromptTemplate.from_messages([
            (
                "system",
                "The user may have asked one or more questions in a single message. "
                "Split the message into separate questions. Return ONLY the questions, one per line. "
                "Do not number them. Do not add labels. If there is exactly one question, return that one line. "
                "If the message is not a question (e.g. greeting), return it as-is on one line. "
                "Example: 'What is X? And what about Y?' -> 'What is X?' then new line 'What about Y?'",
            ),
            ("human", "{input}"),
        ])
        | llm
        | StrOutputParser()
    )

    # 3. Rewrite query (standalone query using chat history)
    rewrite_chain = (
        ChatPromptTemplate.from_messages([
            (
                "system",
                "Rewrite the user message into a standalone search query for finding relevant book content. "
                "Use chat history for context. Output the query in English only. "
                "IMPORTANT: For the same topic, always use the same English search terms so retrieval returns the same excerpts (and same page numbers) whether the user asked in Dutch or English. Examples: recepten/recipes → 'recipes'; dagmenu/daily menu → 'daily menu'; voeding aanpassen/wedstrijd → 'competition nutrition' or 'training day menu'. So 'waar vind ik de recepten?' and 'on which page are the recipes?' must both become a query like 'recipes where in book' so the same pages are found. Return ONLY the rewritten query."
            ),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}")
        ])
        | llm
        | StrOutputParser()
    )

    # 4. Answer generation (validate-then-answer)
    answer_chain = (
        ChatPromptTemplate.from_messages([
            (
                "system",
                f"You are the {settings.BOOK_TITLE} AI assistant. "
                "You will receive excerpts from the book. Each excerpt has a label like [page N] or [section N]. "
                "RULES: 1) Answer ONLY from the excerpts. 2) Answer in the SAME LANGUAGE as the user (Dutch or English). "
                "3) If excerpts contain relevant info (even partial), you MUST answer fully: summarize the actual content (e.g. what an ideal daily menu looks like—meals, examples, timing). Give the substance from the excerpts so the user gets a complete answer. "
                "4) When the user does NOT ask for a reference/source/page: do not add page numbers—just give the content. "
                "5) When the user DOES ask for a reference, source, or page numbers (e.g. 'give reference', 'with reference', 'include source', 'which page', 'welke pagina', 'waar vind ik', 'where can I find', 'give me the page', 'met bron', 'met referentie', 'cite', 'page number', 'bladzijde', in the same message or a follow-up), you MUST include the page/section numbers from the excerpt labels in your answer. Use 'page N' in English (e.g. 'See page 42, 43.') and 'pagina N' in Dutch (e.g. 'Zie pagina 42, 43.'). List all relevant pages from the excerpts you used. "
                "6) ONLY when excerpts have NOTHING relevant to the question, reply with exactly: \"Unfortunately, I can't help you with this question. However, I'm happy to help you with questions about sports nutrition!\" then \"Helaas kan ik je bij deze vraag niet helpen. Wel help ik je graag verder met vragen over sportvoeding!\". Never mix: if you have relevant content, answer only that; if none, use only this refusal. "
            ),
            ("system", "Context excerpts:\n{context}"),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}")
        ])
        | llm
        | StrOutputParser()
    )

    print("✅ RAG components initialized")


# -----------------------------
# GET RESPONSE
# -----------------------------
def _split_into_questions(user_input: str) -> list:
    """Split user message into a list of separate questions. Returns at least one element."""
    if not user_input or not user_input.strip():
        return [user_input or ""]
    if not split_questions_chain:
        return [user_input.strip()]
    try:
        raw = split_questions_chain.invoke({"input": user_input})
        if not raw or not str(raw).strip():
            return [user_input.strip()]
        lines = [s.strip() for s in str(raw).strip().split("\n") if s.strip()]
        return lines if lines else [user_input.strip()]
    except Exception:
        return [user_input.strip()]


def get_response(user_input: str, whatsapp_number: str, db: Session, is_first_message: bool = False):
    if not all([llm, retriever, intent_chain, split_questions_chain, rewrite_chain, answer_chain]):
        init_rag_components()

    # 1. Intent check
    intent = intent_chain.invoke({"input": user_input}).strip().upper()
    if "GREETING" in intent:
        if _use_dutch_page_word(user_input):
            reply = (
                "Hoi! 👋 Ik ben de Eet als een Atleet-assistent. "
                "Ik beantwoord vragen alleen op basis van het boek. "
                "Stel gerust een vraag over voeding, training of recepten."
            )
        else:
            reply = (
                "Hi! 👋 I'm the Eat like an Athlete assistant. "
                "I answer questions only from the book. "
                "Ask me anything about nutrition, training or recipes."
            )
        final = _prepend_welcome_if_first(reply, is_first_message, user_input)
        db.add(ChatLog(whatsapp_number=whatsapp_number, user_message=user_input, bot_response=final, response_type="greeting", chunks_used=[], history_snapshot=[]))
        db.commit()
        return final
    if "THANKS" in intent:
        if _use_dutch_page_word(user_input):
            reply = "Graag gedaan! Stel gerust nog een vraag over het boek."
        else:
            reply = "You're welcome! Ask me anything else about the book."
        final = _prepend_welcome_if_first(reply, is_first_message, user_input)
        db.add(ChatLog(whatsapp_number=whatsapp_number, user_message=user_input, bot_response=final, response_type="thanks", chunks_used=[], history_snapshot=[]))
        db.commit()
        return final

    # 2. Load chat history
    past_logs = (
        db.query(ChatLog)
        .filter(ChatLog.whatsapp_number == whatsapp_number)
        .order_by(desc(ChatLog.created_at))
        .limit(settings.MAX_CHAT_LOG_MESSAGES)
        .all()
    )

    chat_history = []
    for log in reversed(past_logs):
        chat_history.append(HumanMessage(content=log.user_message))
        chat_history.append(AIMessage(content=log.bot_response))

    questions = _split_into_questions(user_input)

    if len(questions) > 1:
        # Multiple questions in one message: answer each and combine into one reply
        def _excerpt_label(meta):
            page = meta.get("page")
            chunk = meta.get("chunk_index", "?")
            section = meta.get("section")
            if page is not None and str(page).strip() and str(page) != "N/A":
                part = f"page {page}"
            else:
                part = f"section {chunk}"
            if section:
                part += f", {section}"
            return part
        parts = []
        all_used_docs = []
        for q in questions:
            if not q.strip():
                continue
            rewritten = rewrite_chain.invoke({"chat_history": [], "input": q})
            try:
                docs_with_scores = retriever.similarity_search_with_score(rewritten, k=settings.RETRIEVAL_TOP_K)
            except OperationalError:
                docs_with_scores = []
            relevant_docs = [(doc, s) for doc, s in docs_with_scores if s <= settings.SIMILARITY_THRESHOLD]
            if not relevant_docs and docs_with_scores:
                relevant_docs = list(docs_with_scores)[:3]
            if not relevant_docs:
                parts.append(_refusal_for_language(q))
            else:
                context_text = "\n\n".join(
                    f"Excerpt [{_excerpt_label(doc.metadata)}]: {doc.page_content}"
                    for doc, _ in relevant_docs
                )
                part = answer_chain.invoke({"context": context_text, "chat_history": [], "input": q})
                part = _strip_refusal_from_answer(part)
                part = _localize_page_citations(q, part)
                all_used_docs.extend(doc.metadata for doc, _ in relevant_docs)
                if _user_asks_for_reference(q) and not _answer_has_page_reference(part):
                    ref_line = _format_references_line([doc.metadata for doc, _ in relevant_docs], use_dutch=_message_suggests_dutch(q))
                    if ref_line:
                        part = (part.rstrip() + "\n\n" + ref_line).strip()
                parts.append(part)
        answer = "\n\n".join(f"{i+1}. {p}" for i, p in enumerate(parts))
        used_docs = all_used_docs
        response_type = "answered"
    else:
        # Single question
        # 3. Rewrite query
        rewritten_query = rewrite_chain.invoke({
            "chat_history": chat_history,
            "input": user_input
        })
        print(f"🔍 DEBUG Rewritten Query: {rewritten_query}")

        # 4. Vector retrieval (with retry on stale DB connection)
        def _do_retrieval():
            return retriever.similarity_search_with_score(
                rewritten_query,
                k=settings.RETRIEVAL_TOP_K
            )

        try:
            docs_with_scores = _do_retrieval()
        except OperationalError as e:
            if "SSL connection" in str(e) or "closed" in str(e).lower() or "connection" in str(e).lower():
                print("⚠️ DB connection stale, re-initializing RAG and retrying once...")
                init_rag_components()
                try:
                    docs_with_scores = _do_retrieval()
                except Exception as retry_e:
                    print(f"❌ Retry failed: {retry_e}")
                    answer = _refusal_for_language(user_input)
                    answer = _prepend_welcome_if_first(answer, is_first_message, user_input)
                    db.add(ChatLog(whatsapp_number=whatsapp_number, user_message=user_input, bot_response=answer, response_type="refused", chunks_used=[], history_snapshot=[]))
                    db.commit()
                    return answer
            else:
                raise

        relevant_docs = [
            (doc, score)
            for doc, score in docs_with_scores
            if score <= settings.SIMILARITY_THRESHOLD
        ]
        # If nothing under threshold, use top 3 retrieved so we don't refuse borderline questions (e.g. wedstrijdperiodes)
        if not relevant_docs and docs_with_scores:
            relevant_docs = list(docs_with_scores)[:3]

        print(f"📊 DEBUG: Found {len(relevant_docs)} relevant chunks")

        # 5. Answer phase
        if not relevant_docs:
            answer = _refusal_for_language(user_input)
            response_type = "refused"
            used_docs = []
        else:
            def _excerpt_label(meta):
                """Label excerpts: page when available, otherwise section N (so we always have a reference to give when asked)."""
                page = meta.get("page")
                chunk = meta.get("chunk_index", "?")
                section = meta.get("section")
                if page is not None and str(page).strip() and str(page) != "N/A":
                    part = f"page {page}"
                else:
                    part = f"section {chunk}"
                if section:
                    part += f", {section}"
                return part
            context_text = "\n\n".join(
                f"Excerpt [{_excerpt_label(doc.metadata)}]: {doc.page_content}"
                for doc, _ in relevant_docs
            )

            answer = answer_chain.invoke({
                "context": context_text,
                "chat_history": chat_history,
                "input": user_input
            })

            # If model wrongly appended refusal despite having relevant excerpts, strip it and keep the useful part
            answer = _strip_refusal_from_answer(answer)
            # Use Dutch 'pagina' instead of 'page' when user wrote in Dutch
            answer = _localize_page_citations(user_input, answer)

            used_docs = [doc.metadata for doc, _ in relevant_docs]
            # If user asked for reference/source/page but the model didn't include one, append references from excerpts
            if _user_asks_for_reference(user_input) and not _answer_has_page_reference(answer):
                ref_line = _format_references_line(used_docs, use_dutch=_message_suggests_dutch(user_input))
                if ref_line:
                    answer = (answer.rstrip() + "\n\n" + ref_line).strip()

            # Only treat as refused if answer is essentially the refusal (no substantive content)
            response_type = _is_refusal_response(answer)

    # 6. Save chat log
    db.add(ChatLog(
        whatsapp_number=whatsapp_number,
        user_message=user_input,
        bot_response=answer,
        response_type=response_type,
        chunks_used=used_docs,
        history_snapshot=[
            {
                "role": "human" if isinstance(m, HumanMessage) else "ai",
                "content": m.content
            }
            for m in chat_history
        ]
    ))
    db.commit()

    # -----------------------------
    # 7. CLEANUP OLD CHAT LOGS (FIXED)
    # -----------------------------
    keep_ids = (
        db.query(ChatLog.id)
        .filter(ChatLog.whatsapp_number == whatsapp_number)
        .order_by(desc(ChatLog.created_at))
        .limit(settings.MAX_CHAT_LOG_MESSAGES)
        .all()
    )

    keep_ids = [id for (id,) in keep_ids]

    if keep_ids:
        (
            db.query(ChatLog)
            .filter(ChatLog.whatsapp_number == whatsapp_number)
            .filter(~ChatLog.id.in_(keep_ids))
            .delete(synchronize_session=False)
        )
        db.commit()

    return _prepend_welcome_if_first(answer, is_first_message, user_input)
