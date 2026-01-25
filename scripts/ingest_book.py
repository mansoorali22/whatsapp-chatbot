import os
import sys
from pathlib import Path


# Resolve project root (whatsapp-chatbot/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from typing import List
from langchain_community.document_loaders import Docx2txtLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from sqlalchemy.orm import Session
from app.db.connection import SessionLocal
from app.db.models import BookChunk
from app.core.config import settings
from app.utils.logger import setup_logging, get_logger

setup_logging()

logger = get_logger(__name__)

def ingest_book(file_path: str):
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return

    logger.info(f"Reading DOCX: {file_path}")
    
    # 1. Load the Word Document
    loader = Docx2txtLoader(file_path)
    documents = loader.load()
    
    # 2. Setup Chunking
    # For DOCX, we split by double newlines first to keep paragraphs together
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", ".", " ", ""]
    )
    
    chunks = text_splitter.split_documents(documents)
    logger.info(f"Created {len(chunks)} chunks from the book.")

    # 3. Setup Embeddings
    embeddings_model = OpenAIEmbeddings(
        model=settings.OPENAI_EMBEDDING_MODEL,
        openai_api_key=settings.OPENAI_API_KEY
    )

    db: Session = SessionLocal()
    
    try:
        # Clear old chunks before re-ingesting (optional but helpful for MVP testing)
        db.query(BookChunk).delete()
        
        for i, chunk in enumerate(chunks):
            # Generate the 1536-dimensional vector
            vector = embeddings_model.embed_query(chunk.page_content)
            
            # Save to Database
            new_chunk = BookChunk(
                content=chunk.page_content,
                embedding=vector,
                metadata_json={
                    "source": settings.BOOK_TITLE,
                    "chunk_index": i
                    # Note: DOCX doesn't have "page" numbers like PDF, 
                    # so we track the chunk order instead.
                }
            )
            db.add(new_chunk)
            
            if i % 15 == 0:
                logger.info(f"Stored {i}/{len(chunks)} chunks...")
        
        db.commit()
        logger.info("Success! Your 'Atleet Buddy' now has the book in its memory.")
        
    except Exception as e:
        db.rollback()
        logger.error(f"Ingestion failed: {e}")
    finally:
        db.close()


FILE_NAME = "C:\\Users\\Abdullah Masood\\Desktop\\WhatsApp RAG\\whatsapp-chatbot\\Eet_als_een_atleet_2023_8e druk.docx"
ingest_book(FILE_NAME)