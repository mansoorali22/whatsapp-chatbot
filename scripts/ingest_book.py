import os
import re
import sys
from pathlib import Path
from sqlalchemy import create_engine, text

# --- PATH FIX: Ensures 'app' is findable ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_core.documents import Document

# Internal App Imports
from app.core.config import settings
from app.utils.logger import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# PDF PAGE-BY-PAGE LOADER
# ---------------------------------------------------------------------------
def _load_pdf_pages(file_path: str) -> list[Document]:
    """
    Extract text from each page of a PDF using PyMuPDF (fitz).
    Returns one Document per page with metadata: {"page": N}.
    Images and infographics are skipped — only text is extracted.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(file_path)
    pages = []
    for i in range(doc.page_count):
        page_text = doc[i].get_text().strip()
        if page_text:
            pages.append(Document(
                page_content=page_text,
                metadata={"page": i + 1}  # 1-based page number
            ))
    doc.close()
    logger.info(f"📄 Extracted text from {len(pages)} pages (of {doc.page_count} total)")
    return pages


# ---------------------------------------------------------------------------
# DOCX / TXT FALLBACK LOADER (kept for backwards compatibility)
# ---------------------------------------------------------------------------
def _load_docx_or_txt(file_path: str) -> list[Document]:
    """Fallback loader for .docx and .txt files (no real page numbers)."""
    if file_path.lower().endswith(".txt"):
        logger.info(f"📖 Reading TXT: {file_path}")
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
        return [Document(page_content=content, metadata={"page": None})]
    else:
        from langchain_community.document_loaders import Docx2txtLoader
        logger.info(f"📖 Reading DOCX: {file_path}")
        loader = Docx2txtLoader(file_path)
        docs = loader.load()
        for d in docs:
            d.metadata["page"] = None
        return docs


# ---------------------------------------------------------------------------
# CHUNKING WITH REAL PAGE NUMBERS
# ---------------------------------------------------------------------------
def _chunk_pdf_pages(page_docs: list[Document]) -> list[Document]:
    """
    Split each page's text into chunks (max 1000 chars, 150 overlap).
    Every chunk inherits the page number of its source page.
    When a chunk spans a page boundary (due to overlap), it gets the page
    number of the page where the chunk *starts*.
    """
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", ".", " ", ""]
    )

    all_chunks = []
    chunk_index = 0

    for page_doc in page_docs:
        page_num = page_doc.metadata["page"]
        page_chunks = text_splitter.split_text(page_doc.page_content)

        for chunk_text in page_chunks:
            all_chunks.append(Document(
                page_content=chunk_text,
                metadata={
                    "source": settings.BOOK_TITLE,
                    "chunk_index": chunk_index,
                    "document_type": "book",
                    "page": page_num,
                }
            ))
            chunk_index += 1

    return all_chunks


def _chunk_legacy_docs(documents: list[Document]) -> list[Document]:
    """Chunk a single-document .docx/.txt load (no real page numbers)."""
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", ".", " ", ""]
    )
    chunks = text_splitter.split_documents(documents)
    total_chunks = len(chunks)
    total_pages = getattr(settings, "BOOK_TOTAL_PAGES", 192) or 192

    pgvector_documents = []
    for i, chunk in enumerate(chunks):
        # Estimate page number (linear spread — inaccurate but better than nothing)
        page = 1 + round((i * (total_pages - 1)) / max(1, total_chunks - 1)) if total_chunks > 1 else 1
        pgvector_documents.append(Document(
            page_content=chunk.page_content,
            metadata={
                "source": settings.BOOK_TITLE,
                "chunk_index": i,
                "document_type": "book",
                "page": page,
            }
        ))
    return pgvector_documents


# ---------------------------------------------------------------------------
# MAIN INGESTION
# ---------------------------------------------------------------------------
def ingest_book_to_pgvector_only(file_path: str):
    """
    Ingests a book (PDF preferred, DOCX/TXT fallback) into PGVector.
    PDF: extracts text page-by-page so every chunk has a real page number.
    DOCX/TXT: falls back to estimated page numbers.
    """

    if not os.path.exists(file_path):
        logger.error(f"❌ File not found at: {file_path}")
        return

    # 1. Load and chunk based on file type
    if file_path.lower().endswith(".pdf"):
        logger.info(f"📖 Reading PDF: {file_path}")
        page_docs = _load_pdf_pages(file_path)
        pgvector_documents = _chunk_pdf_pages(page_docs)
    else:
        documents = _load_docx_or_txt(file_path)
        pgvector_documents = _chunk_legacy_docs(documents)

    logger.info(f"✂️ Created {len(pgvector_documents)} chunks from the book.")

    # 2. Setup Embeddings
    embeddings_model = OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=settings.OPENAI_API_KEY
    )

    # 3. Clear existing embeddings safely
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

    # 4. Ingest into PGVector
    logger.info("🚀 Ingesting into PGVector (Neon)...")
    vectorstore = PGVector.from_documents(
        documents=pgvector_documents,
        embedding=embeddings_model,
        collection_name="book_chunks",
        connection=settings.DATABASE_URL,
        use_jsonb=True,
    )

    logger.info("✅ Success! Data successfully stored in 'langchain_pg_embedding'.")

    # 5. Verification Search
    test_docs = vectorstore.similarity_search("What are the key points of this guide?", k=2)
    logger.info(f"🔍 Test retrieval found {len(test_docs)} relevant documents.")
    for i, doc in enumerate(test_docs):
        logger.info(f"📄 Sample {i+1} Metadata: {doc.metadata}")


if __name__ == "__main__":
    # Priority: PDF > DOCX > TXT
    pdf_path = PROJECT_ROOT / "Eet_als_een_atleet_001-192.pdf"
    docx_path = PROJECT_ROOT / "Eet_als_een_atleet_2023_8e druk.docx"
    txt_path = PROJECT_ROOT / "Eet_als_een_atleet_2023_8e druk_tekst.txt"

    if pdf_path.exists():
        file_path = str(pdf_path)
    elif docx_path.exists():
        file_path = str(docx_path)
    elif txt_path.exists():
        file_path = str(txt_path)
    else:
        logger.error(
            "❌ No book file found. Add one of:\n"
            "  - Eet_als_een_atleet_001-192.pdf (preferred)\n"
            "  - Eet_als_een_atleet_2023_8e druk.docx\n"
            "  - Eet_als_een_atleet_2023_8e druk_tekst.txt"
        )
        sys.exit(1)

    ingest_book_to_pgvector_only(file_path)
