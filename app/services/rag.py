from sqlalchemy.orm import Session
from sqlalchemy import desc

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser

from app.core.config import settings
from app.db.models import ChatLog

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
                "You will receive excerpts from the book. "
                "RULES: 1) Answer ONLY from the excerpts. 2) Answer in the SAME LANGUAGE as the user (Dutch or English). "
                "3) If excerpts contain relevant info (even partial), use it. Include section/page when in labels. "
                "4) For follow-up (e.g. on which page?, waar vind ik dat?), use excerpt labels or content. "
                "5) Say 'I don't know. This is outside the book's context.' ONLY when excerpts clearly have nothing relevant. "
                "If the excerpts help, summarize clearly. "
                "If not, say exactly: 'I don’t know. This is outside the book’s context.'"
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

    # 4. Vector retrieval
    docs_with_scores = retriever.similarity_search_with_score(
        rewritten_query,
        k=settings.RETRIEVAL_TOP_K
    )

    relevant_docs = [
        (doc, score)
        for doc, score in docs_with_scores
        if score <= settings.SIMILARITY_THRESHOLD
    ]

    print(f"📊 DEBUG: Found {len(relevant_docs)} relevant chunks")

    # 5. Answer phase
    if not relevant_docs:
        answer = "I don’t know. This is outside the book’s context."
        response_type = "refused"
        used_docs = []
    else:
        context_text = "\n\n".join(
            f"Excerpt [chunk {doc.metadata.get('chunk_index', '?')}, page {doc.metadata.get('page', 'N/A')}, section {doc.metadata.get('section', 'N/A')}]: {doc.page_content}"
            for doc, _ in relevant_docs
        )

        answer = answer_chain.invoke({
            "context": context_text,
            "chat_history": chat_history,
            "input": user_input
        })

        response_type = "refused" if "I don’t know" in answer or "Ik weet het niet" in answer or "buiten de context" in answer.lower() else "answered"
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
