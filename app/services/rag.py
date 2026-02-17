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

# Welcome message for new users (Dutch)
WELCOME_INTRO_NL = (
    "Ik beantwoord graag al je vragen over sportvoeding, herstel, gezonde voeding en recept inspiratie. "
    "Verwacht praktische tips, evidence-based advies en ideeën die je meteen kunt toepassen in je keuken en sport voorbereiding!"
)

# Out-of-context reply (bilingual)
REFUSAL_MESSAGE = (
    "Unfortunately, I can't help you with this question. However, I'm happy to help you with questions about sports nutrition!\n\n"
    "Helaas kan ik je bij deze vraag niet helpen. Wel help ik je graag verder met vragen over sportvoeding!"
)


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


def _use_dutch_page_word(user_message: str) -> bool:
    """True if we should use 'pagina' instead of 'page' in citations (user writes in Dutch or config is Dutch)."""
    if not user_message or not user_message.strip():
        return False
    lang = getattr(settings, "DEFAULT_LANGUAGE", "") or ""
    if "dutch" in lang.lower() or lang.lower() == "nl":
        return True
    msg = user_message.lower().strip()
    dutch_cues = [
        "pagina", "welke", "waar", "bladzijde", "vind", "staat", "recept", "het boek",
        "een vraag", "van de", "op welke", "welke pagina", "kunt u", "kun je",
        "graag", "alsjeblieft", "dank", "bedankt", "hoeveel", "waarom", "wanneer",
    ]
    return any(c in msg for c in dutch_cues)


def _localize_page_citations(user_message: str, answer: str) -> str:
    """Replace English 'page N' with Dutch 'pagina N' when the user is writing in Dutch."""
    if not answer or not _use_dutch_page_word(user_message):
        return answer
    # "page 196" / "page 197" -> "pagina 196" / "pagina 197"
    answer = re.sub(r"\bpage\s+(\d+)", r"pagina \1", answer, flags=re.IGNORECASE)
    # "pages 1-5" / "pages 196, 197" -> "pagina's"
    answer = re.sub(r"\bpages\s+", "pagina's ", answer, flags=re.IGNORECASE)
    return answer


def _prepend_welcome_if_first(reply: str, is_first_message: bool) -> str:
    """Prepend the Dutch welcome intro when this is the user's first message."""
    if not is_first_message or not reply:
        return WELCOME_INTRO_NL if (is_first_message and not reply) else reply
    return WELCOME_INTRO_NL + "\n\n" + reply


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
rewrite_chain = None
answer_chain = None


# -----------------------------
# INIT RAG COMPONENTS
# -----------------------------
def init_rag_components():
    global llm, retriever, intent_chain, rewrite_chain, answer_chain

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
             "Treat 'who are you', 'how does this work', 'hoe werkt dit', 'wie ben jij' as GREETING. "
             "Treat 'thanks', 'thank you', 'bedankt', 'dank je', 'dankjewel' as THANKS (not GREETING)."),
            ("human", "{input}")
        ])
        | llm
        | StrOutputParser()
    )

    # 2. Rewrite query (standalone query using chat history)
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

    # 3. Answer generation (validate-then-answer)
    answer_chain = (
        ChatPromptTemplate.from_messages([
            (
                "system",
                f"You are the {settings.BOOK_TITLE} AI assistant. "
                "You will receive excerpts from the book. Each excerpt has a label like [page N] or [section N]. "
                "RULES: 1) Answer ONLY from the excerpts. 2) Answer in the SAME LANGUAGE as the user (Dutch or English). "
                "3) If excerpts contain relevant info (even partial), you MUST answer fully: summarize the actual content (e.g. what an ideal daily menu looks like—meals, examples, timing). Give the substance from the excerpts so the user gets a complete answer. "
                "4) NEVER mention page numbers, 'page N', 'see page', or 'pagina' in your answer UNLESS the user explicitly asked for them (e.g. 'on which page?', 'waar vind ik dat?', 'welke bladzijde?', 'where can I find that?'). If they did NOT ask for a page, do not add any page reference—just give the content. "
                "5) ONLY when the user explicitly asks where to find something (page/bladzijde/waar vind ik), give the page numbers from the excerpt labels. If the user wrote in Dutch, use 'pagina N' (e.g. 'pagina 179, pagina 186'). If in English, use 'page N'. Same topic = same page numbers. NEVER say I don't know for this; it is in scope. "
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
def get_response(user_input: str, whatsapp_number: str, db: Session, is_first_message: bool = False):
    if not all([llm, retriever, intent_chain, rewrite_chain, answer_chain]):
        init_rag_components()

    # 1. Intent check
    intent = intent_chain.invoke({"input": user_input}).strip().upper()
    if "GREETING" in intent:
        reply = (
            "Hoi! 👋 Ik ben de Eet als een Atleet-assistent. "
            "Ik beantwoord vragen alleen op basis van het boek. "
            "Stel gerust een vraag over voeding, training of recepten."
        )
        return _prepend_welcome_if_first(reply, is_first_message)
    if "THANKS" in intent:
        reply = "Graag gedaan! Stel gerust nog een vraag over het boek."
        return _prepend_welcome_if_first(reply, is_first_message)

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
                answer = REFUSAL_MESSAGE
                answer = _prepend_welcome_if_first(answer, is_first_message)
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
        answer = REFUSAL_MESSAGE
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

        # Only treat as refused if answer is essentially the refusal (no substantive content)
        response_type = _is_refusal_response(answer)
        used_docs = [doc.metadata for doc, _ in relevant_docs]

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

    return _prepend_welcome_if_first(answer, is_first_message)
