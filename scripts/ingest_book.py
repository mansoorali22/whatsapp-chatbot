import os
import re
import sys
from pathlib import Path
from bisect import bisect_right
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


# ---------------------------------------------------------------------------
# LANDMARK-BASED PAGE MAPPING
# ---------------------------------------------------------------------------
# These are section headings and recipe titles from the book's own table of
# contents and recipe index ("Receptenregister"), paired with their real page
# numbers.  When ingesting, we locate each landmark in the full text to build
# a (character-position → page-number) mapping.  Each chunk is then assigned
# the page of the nearest preceding landmark.
# ---------------------------------------------------------------------------
_BOOK_LANDMARKS = [
    # --- Table of Contents sections ---
    ("Altijd op zoek naar verbetering", 6),
    ("Haal het optimale uit jezelf", 8),
    ("Welk personage ligt het dichtst bij jou", 12),
    ("Genoeg eten is essentieel", 18),
    ("Basisvoedingsmiddelen voor prestatie en herstel", 29),
    ("De verschillende brandstoffen voor je lichaam", 29),
    ("De noodzaak van eiwitten", 36),
    ("Je kunt niet lang zonder drinken", 49),
    ("Rondom het sporten heb je andere voeding nodig", 56),
    ("Eten voor het sporten", 56),
    ("Drinken voor het sporten", 83),
    ("Eten tijdens het sporten", 86),
    ("Drinken tijdens het sporten", 96),
    ("Eten en drinken na het sporten", 105),
    ("voor de laatste paar procent", 128),
    ("Bewezen werkzame supplementen", 135),
    ("Voedingssupplementen: Als je iets tekort komt", 135),
    ("Prestatiebevorderende supplementen", 142),
    ("Gewichtsverlies en gewichtstoename", 150),
    ("Verstoord eetgedrag", 155),
    ("Hoogte, kou en hitte", 157),
    ("Voeding op reis", 161),
    ("Voeding tijdens blessures", 162),
    ("De vegetarische of veganistisch sporter", 164),
    ("Hoe eten de atleten", 178),
    ("Schrijven als een atleet", 188),
    ("Over de auteurs", 189),
    # --- Recipe index entries (real page numbers from the book's Receptenregister) ---
    # Use the uppercase recipe headers (e.g. "BANANEN-RICOTTA PANNENKOEKJES") to
    # match the actual recipe content, not the title listing that precedes them.
    ("QUINOA PARFAIT", 47),
    ("VRUCHTENKWARK", 47),
    ("BANANEN-RICOTTA PANNENKOEKJES", 47),
    ("SPORTOMELET", 48),
    ("TEFFPAP", 48),
    ("Infused water", 54),
    ("CLOUDIES", 72),
    ("COUSCOUSSALADE  VOOR", 79),
    ("SOBA NOEDELS", 80),
    ("BULGURSALADE MET KIP", 80),
    ("WRAPS MET ZALM", 81),
    ("PASTA MET TEMPEH", 81),
    ("Kwarktaart", 111),
    ("CHOCOLADEMELK    VOOR", 116),
    ("Edamame hummus", 113),
    ("BONENSCHOTEL", 121),
    ("Pasta met zalm", 120),
    ("THAISE KIPCURRY", 120),
    ("SPORTQUICHE", 112),
    ("Vruchtenijs", 111),
    ("OVERNIGHT APPELTAART-OATS", 66),
    ("CHUNKY MONKEY OATS", 66),
    ("Homemade sportgranola", 67),
    ("VANILLE RIJSTEBRIJ", 71),
    ("Boekweitpannenkoek", 66),
    # Vegetarian recipes
    ("ROEREI ZONDER EI", 167),
    ("SPORTFALAFEL", 170),
    ("KNAPPERIGE TEMPEH UIT DE OVEN", 172),
]


def _build_page_map(full_text: str):
    """
    Locate each landmark in *full_text* (first occurrence only, skipping the
    recipe-index section at the end) and return a sorted list of
    (char_position, page_number) tuples.
    """
    # Ignore the recipe index near the end (it repeats recipe names with page
    # numbers, which would create duplicate matches at the wrong position).
    index_start = full_text.find("Receptenregister Dranken")
    if index_start == -1:
        index_start = len(full_text)

    entries = []
    for title, page in _BOOK_LANDMARKS:
        # Replace runs of whitespace in the landmark with a flexible \s+ pattern
        # so "COUSCOUSSALADE  VOOR" matches "COUSCOUSSALADE\n\nVOOR" etc.
        pattern = r"\s+".join(re.escape(w) for w in title.split())
        m = re.search(pattern, full_text[:index_start], re.IGNORECASE)
        if m:
            entries.append((m.start(), page))
        else:
            logger.debug(f"Landmark not found (skipped): {title}")

    # Sort by position and deduplicate
    entries.sort(key=lambda x: x[0])
    logger.info(f"📍 Located {len(entries)} of {len(_BOOK_LANDMARKS)} page landmarks in text")
    return entries


def _page_for_position(page_map: list, pos: int, fallback_page: int = 1) -> int:
    """
    Given a sorted page_map [(pos, page), ...] and a character position,
    return the page number of the nearest preceding landmark.
    """
    if not page_map:
        return fallback_page
    positions = [p for p, _ in page_map]
    idx = bisect_right(positions, pos) - 1
    if idx < 0:
        return fallback_page
    return page_map[idx][1]

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

    # 5. Prepare LangChain Documents with real page numbers from landmarks
    # Build a (position → page) map from the book's own TOC and recipe index
    full_text = "\n\n".join(doc.page_content for doc in documents)
    page_map = _build_page_map(full_text)

    # Track cumulative character offset so we can map each chunk back to the
    # full text and look up the correct page.
    chunk_offset = 0
    pgvector_documents = []
    for i, chunk in enumerate(chunks):
        # Find where this chunk starts in the full text (search from last offset)
        pos = full_text.find(chunk.page_content[:80], chunk_offset)
        if pos == -1:
            # Fallback: try from the beginning (overlap can cause skips)
            pos = full_text.find(chunk.page_content[:80])
        if pos != -1:
            chunk_offset = pos
        page = _page_for_position(page_map, chunk_offset)
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