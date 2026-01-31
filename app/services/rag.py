# app/services/rag.py
import sys
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import desc

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser

from app.core.config import settings
from app.db.models import ChatLog

# Placeholders
llm = None
retriever = None
context_chain = None
answer_chain = None

def init_rag_components():
    global llm, retriever, context_chain, answer_chain

    embeddings = OpenAIEmbeddings(model=settings.OPENAI_EMBEDDING_MODEL)

    llm = ChatOpenAI(
        model=settings.OPENAI_MODEL,
        temperature=0,
        max_tokens=settings.MAX_TOKENS_RESPONSE
    )

    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name="book_chunks",
        connection=settings.DATABASE_URL,
        use_jsonb=True,
    )

    retriever = vectorstore.as_retriever(search_kwargs={"k": settings.RETRIEVAL_TOP_K})

    # Chains
    context_chain = ChatPromptTemplate.from_messages([
        ("system", "You are a search query optimizer. Rewrite the message into a standalone search query."),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}")
    ]) | llm | StrOutputParser()

    answer_chain = ChatPromptTemplate.from_messages([
        ("system", f"You are the {settings.BOOK_TITLE} Buddy AI. ONLY answer based on context:\n{{context}}"),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}")
    ]) | llm | StrOutputParser()

    print("✅ RAG Components initialized.")

def get_response(user_input: str, whatsapp_number: str, db: Session):
    """Generate RAG-based answer for a WhatsApp message."""
    if not all([llm, retriever, context_chain, answer_chain]):
        raise ValueError("RAG components not initialized. Call init_rag_components() first.")

    # Fetch recent chat history
    past_logs = db.query(ChatLog).filter(
        ChatLog.whatsapp_number == whatsapp_number
    ).order_by(desc(ChatLog.created_at)).limit(5).all()

    chat_history = []
    for log in reversed(past_logs):
        chat_history.append(HumanMessage(content=log.user_message))
        chat_history.append(AIMessage(content=log.bot_response))

    # Rewritten query
    standalone_q = context_chain.invoke({"chat_history": chat_history, "input": user_input})

    # Retrieval
    docs = retriever.invoke(standalone_q)
    if not docs:
        docs = retriever.invoke(user_input)

    if not docs:
        answer = "I'm sorry, I don't have information on that in the guide."
        source_documents = []
    else:
        context_text = "\n\n".join([f"[Page {d.metadata.get('page', 'N/A')}]: {d.page_content}" for d in docs])
        answer = answer_chain.invoke({"context": context_text, "chat_history": chat_history, "input": user_input})
        source_documents = docs

    # Log to DB
    response_type = "answered" if "I'm sorry" not in answer else "refused"
    new_log = ChatLog(
        whatsapp_number=whatsapp_number,
        user_message=user_input,
        bot_response=answer,
        response_type=response_type,
        chunks_used=[doc.metadata for doc in source_documents],
        history_snapshot=[{"role": "human" if isinstance(m, HumanMessage) else "ai", "content": m.content} for m in chat_history]
    )
    db.add(new_log)
    db.commit()

    return answer
