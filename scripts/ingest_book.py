import os
import sys
from pathlib import Path
from sqlalchemy import create_engine, text

# --- PATH FIX: Ensures 'app' is findable ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_community.document_loaders import Docx2txtLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_core.documents import Document

# Internal App Imports
from app.core.config import settings
from app.utils.logger import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

def ingest_book_to_pgvector_only(file_path: str):
    """
    Ingests a DOCX book into PGVector, maintaining the 'book_chunks' 
    collection context required by LangChain.
    """
    
    if not os.path.exists(file_path):
        logger.error(f"❌ File not found at: {file_path}")
        return

    # 1. Load: DOCX or TXT
    if file_path.lower().endswith(".txt"):
        logger.info(f"📖 Reading TXT: {file_path}")
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
        documents = [Document(page_content=content)]
    else:
        logger.info(f"📖 Reading DOCX: {file_path}")
        loader = Docx2txtLoader(file_path)
        documents = loader.load()
    
    # 2. Setup Chunking logic
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", ".", " ", ""]
    )
    
    chunks = text_splitter.split_documents(documents)
    logger.info(f"✂️ Created {len(chunks)} chunks from the book.")

    # 3. Setup Embeddings (Using text-embedding-3-small for efficiency)
    embeddings_model = OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=settings.OPENAI_API_KEY
    )

    # 4. Clear existing embeddings safely
    # We target embeddings linked to 'book_chunks' without dropping tables
    engine = create_engine(settings.DATABASE_URL)
    with engine.connect() as conn:
        logger.info("🧹 Cleaning up existing 'book_chunks' data in Neon...")
        try:
            conn.execute(text("""
                DELETE FROM langchain_pg_embedding 
                WHERE collection_id IN (
                    SELECT uuid FROM langchain_pg_collection WHERE name = 'book_chunks'
                )
            """))
            conn.commit()
            logger.info("✨ Old embeddings cleared.")
        except Exception as e:
            logger.warning(f"⚠️ Cleanup skipped (likely first run): {e}")

    # 5. Prepare LangChain Documents (assign estimated page so every answer can cite "page N")
    total_chunks = len(chunks)
    total_pages = getattr(settings, "BOOK_TOTAL_PAGES", 250) or 250
    pgvector_documents = []
    for i, chunk in enumerate(chunks):
        # Spread chunks across the book so each has an estimated page number
        page = 1 + round((i * (total_pages - 1)) / max(1, total_chunks - 1)) if total_chunks > 1 else 1
        pgvector_documents.append(Document(
            page_content=chunk.page_content,
            metadata={
                "source": settings.BOOK_TITLE,
                "chunk_index": i,
                "document_type": "book",
                "page": page
            }
        ))
    
    # 6. Ingest into PGVector
    logger.info("🚀 Ingesting into PGVector (Neon)...")
    vectorstore = PGVector.from_documents(
        documents=pgvector_documents,
        embedding=embeddings_model,
        collection_name="book_chunks",
        connection=settings.DATABASE_URL,
        use_jsonb=True,
    )
    
    logger.info("✅ Success! Data successfully stored in 'langchain_pg_embedding'.")
    
    # 7. Verification Search
    test_docs = vectorstore.similarity_search("What are the key points of this guide?", k=2)
    logger.info(f"🔍 Test retrieval found {len(test_docs)} relevant documents.")
    for i, doc in enumerate(test_docs):
        logger.info(f"📄 Sample {i+1} Metadata: {doc.metadata}")

if __name__ == "__main__":
    # Book in project folder: try .docx first, then .txt
    docx_path = PROJECT_ROOT / "Eet_als_een_atleet_2023_8e druk.docx"
    txt_path = PROJECT_ROOT / "Eet_als_een_atleet_2023_8e druk_tekst.txt"
    if docx_path.exists():
        file_path = str(docx_path)
    elif txt_path.exists():
        file_path = str(txt_path)
    else:
        logger.error("❌ No book file found. Add Eet_als_een_atleet_2023_8e druk.docx or Eet_als_een_atleet_2023_8e druk_tekst.txt to the project folder.")
        sys.exit(1)
    ingest_book_to_pgvector_only(file_path)