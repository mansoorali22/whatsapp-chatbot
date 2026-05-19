"""
D/E5 -- FAQ Cache service.

Caches frequent question-answer pairs so repeated questions can be answered
instantly without a full vector search + LLM call. Uses SHA-256 hash of the
normalized question for exact matching.

Flow:
  1. Before retrieval: check cache via hash -> if hit, return cached answer
  2. After generating an answer: upsert into cache (increment hit_count if exists)
"""

import hashlib
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.db.models import FAQCache


def normalize_question(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for consistent hashing."""
    cleaned = re.sub(r"[^\w\s]", "", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _hash_question(text: str) -> str:
    return hashlib.sha256(normalize_question(text).encode()).hexdigest()


def get_cached_answer(db: Session, question: str) -> str | None:
    """
    Look up a cached answer by exact hash match.
    Returns the cached answer text or None.
    Increments hit_count on cache hit.
    """
    q_hash = _hash_question(question)
    entry = db.query(FAQCache).filter_by(question_hash=q_hash).first()

    if entry and entry.answer_text:
        entry.hit_count += 1
        entry.last_hit_at = datetime.now(timezone.utc)
        try:
            db.commit()
        except Exception:
            db.rollback()
        return entry.answer_text

    return None


def cache_answer(
    db: Session,
    question: str,
    answer: str,
    language: str,
) -> None:
    """
    Cache (or update) a question-answer pair.
    If the question already exists in cache, update the answer and bump hit_count.
    Otherwise create a new entry.
    """
    q_hash = _hash_question(question)

    existing = db.query(FAQCache).filter_by(question_hash=q_hash).first()
    if existing:
        # Update answer (may have improved) and bump count
        existing.answer_text = answer
        existing.hit_count += 1
        existing.last_hit_at = datetime.now(timezone.utc)
    else:
        db.add(FAQCache(
            question_hash=q_hash,
            question_text=question,
            answer_text=answer,
            language=language,
            hit_count=1,
        ))

    try:
        db.commit()
    except Exception:
        db.rollback()
