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

# Out-of-context reply (bilingual)
REFUSAL_MESSAGE = (
    "Unfortunately, I can't help you with this question. However, I'm happy to help you with questions about sports nutrition!\n\n"
    "Helaas kan ik je bij deze vraag niet helpen. Wel help ik je graag verder met vragen over sportvoeding!"
)


def _strip_refusal_from_answer(answer: str) -> str:
    """Remove refusal phrases if the model wrongly appended them after useful content."""
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
    # Also strip trailing sentence that is only the old refusal
    for old in ("I don't know. This is outside the book's context.", "Ik weet het niet. Dit is buiten de context van het boek."):
        if out.endswith(old):
            out = out[: -len(old)].strip()
            break
    return out if out else answer


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
    # If answer contains refusal but also substantive content (e.g. chunk 13), treat as answered
    if "chunk" in a.lower() or "excerpt" in a.lower() or "page" in a.lower():
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
            ("system", "Classify the user message. Return ONLY one word: GREETING or QUESTION. "
             "Treat 'who are you', 'how does this work', 'hoe werkt dit', 'wie ben jij' as GREETING."),
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
                "Use chat history for context. Keep the query in English for search. Return ONLY the rewritten query."
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
                "You will receive excerpts from the book. Each excerpt has a label like [page N] or [page number not in index]. "
                "RULES: 1) Answer ONLY from the excerpts. 2) Answer in the SAME LANGUAGE as the user (Dutch or English). "
                "3) If excerpts contain relevant info (even partial), you MUST answer from them. Do NOT say I don't know or outside context when you have relevant excerpts. When citing, always refer to PAGE NUMBER when the label has one (e.g. 'see page 42'); never mention chunk numbers to the user. "
                "4) For 'on which page?' or 'waar vind ik dat?': if the label says [page N], say that page. If the label says [page number not in index], say the content is in the book but the exact page number is not provided in the index—do NOT mention chunks or add I don't know. "
                "5) ONLY when excerpts have NOTHING relevant, reply with exactly: \"Unfortunately, I can't help you with this question. However, I'm happy to help you with questions about sports nutrition!\" then \"Helaas kan ik je bij deze vraag niet helpen. Wel help ik je graag verder met vragen over sportvoeding!\". Never mix: if you have relevant content, answer only that; if none, use only this refusal. "
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
def get_response(user_input: str, whatsapp_number: str, db: Session):
    if not all([llm, retriever, intent_chain, rewrite_chain, answer_chain]):
        init_rag_components()

    # 1. Intent check
    intent = intent_chain.invoke({"input": user_input}).strip().upper()
    if "GREETING" in intent:
        return (
            "Hoi! 👋 Ik ben de Eet als een Atleet-assistent. "
            "Ik beantwoord vragen alleen op basis van het boek. "
            "Stel gerust een vraag over voeding, training of recepten. "
            "Hi! I'm the Eat like an Athlete assistant. I answer only from the book. Ask me anything about nutrition, training or recipes."
        )

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

    print(f"📊 DEBUG: Found {len(relevant_docs)} relevant chunks")

    # 5. Answer phase
    if not relevant_docs:
        answer = REFUSAL_MESSAGE
        response_type = "refused"
        used_docs = []
    else:
        def _excerpt_label(meta):
            """Label excerpts by page when available; otherwise 'page number not in index' so we never show chunk numbers to the user."""
            page = meta.get("page")
            section = meta.get("section")
            if page is not None and str(page).strip() and str(page) != "N/A":
                part = f"page {page}"
            else:
                part = "page number not in index"
            if section:
                part += f", section {section}"
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

    return answer
