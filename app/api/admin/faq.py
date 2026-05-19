"""
D/E5 -- Admin FAQ cache management endpoints.

GET    /faq            -- list cached Q&A pairs (sorted by hit_count desc)
PATCH  /faq/{id}       -- edit a cached answer
DELETE /faq/{id}       -- delete a cached entry
GET    /faq/stats      -- cache stats (total entries, total hits, top questions)
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from pydantic import BaseModel
from datetime import datetime

from app.db.connection import get_db
from app.db.models import FAQCache, AdminUser
from app.middleware.admin_auth import get_current_admin

router = APIRouter()


# ── Schemas ──

class FAQOut(BaseModel):
    id: int
    question_text: str
    answer_text: str
    language: str | None
    hit_count: int
    last_hit_at: datetime | None
    created_at: datetime | None

    class Config:
        from_attributes = True


class FAQListResponse(BaseModel):
    items: list[FAQOut]
    total: int
    page: int
    pages: int


class FAQUpdate(BaseModel):
    answer_text: str


class FAQStatsResponse(BaseModel):
    total_entries: int
    total_hits: int
    cache_hit_savings_usd: float  # rough estimate of saved API cost


# ── Endpoints ──

@router.get("/faq", response_model=FAQListResponse)
def list_faq(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """List cached FAQ entries sorted by hit count."""
    q = db.query(FAQCache)
    total = q.count()
    pages = max(1, (total + per_page - 1) // per_page)

    items = (
        q.order_by(desc(FAQCache.hit_count))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return FAQListResponse(
        items=[FAQOut.model_validate(i) for i in items],
        total=total,
        page=page,
        pages=pages,
    )


@router.get("/faq/stats", response_model=FAQStatsResponse)
def faq_stats(
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Cache statistics."""
    total_entries = db.query(func.count(FAQCache.id)).scalar() or 0
    total_hits = db.query(func.coalesce(func.sum(FAQCache.hit_count), 0)).scalar() or 0
    # Rough savings estimate: each cache hit saves ~$0.0002 (avg gpt-4o-mini call)
    savings = float(total_hits) * 0.0002

    return FAQStatsResponse(
        total_entries=total_entries,
        total_hits=total_hits,
        cache_hit_savings_usd=round(savings, 4),
    )


@router.patch("/faq/{faq_id}", response_model=FAQOut)
def update_faq(
    faq_id: int,
    body: FAQUpdate,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Edit a cached answer."""
    entry = db.query(FAQCache).filter_by(id=faq_id).first()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FAQ entry not found")

    entry.answer_text = body.answer_text
    db.commit()
    db.refresh(entry)
    return FAQOut.model_validate(entry)


@router.delete("/faq/{faq_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_faq(
    faq_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Delete a cached FAQ entry."""
    entry = db.query(FAQCache).filter_by(id=faq_id).first()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="FAQ entry not found")

    db.delete(entry)
    db.commit()
