"""
B4 — Refusal analytics endpoints.

Endpoints (all require admin or support JWT):
  GET /admin/refusals/summary?from=YYYY-MM-DD&to=YYYY-MM-DD
  GET /admin/refusals/grouped?from=...&to=...
  GET /admin/refusals/trend?from=...&to=...&interval=day
  GET /admin/refusals/list?from=...&to=...&page=1&per_page=50
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Date, cast, desc, func
from sqlalchemy.orm import Session

from app.db.connection import get_db
from app.db.models import ChatLog
from app.middleware.admin_auth import get_current_admin

router = APIRouter(prefix="/refusals", tags=["Admin Refusals"])


def require_staff(admin: Any = Depends(get_current_admin)) -> Any:
    if getattr(admin, "role", None) not in ("admin", "support"):
        raise HTTPException(status_code=403, detail="Staff role required")
    return admin


def _parse_range(
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = Query(None),
) -> tuple[datetime, datetime]:
    if from_ is None or to is None:
        raise HTTPException(status_code=400, detail="Query params 'from' and 'to' are required (YYYY-MM-DD)")
    if to < from_:
        raise HTTPException(status_code=400, detail="'to' must be on or after 'from'")
    start = datetime(from_.year, from_.month, from_.day, tzinfo=timezone.utc)
    end_exclusive = datetime(to.year, to.month, to.day, tzinfo=timezone.utc) + timedelta(days=1)
    return start, end_exclusive


# ── Schemas ──

class RefusalSummaryOut(BaseModel):
    total_refusals: int
    total_messages: int
    refusal_rate: float  # 0.0 - 1.0
    unique_users_refused: int


class RefusalGroupedOut(BaseModel):
    category: str
    count: int
    percentage: float


class RefusalTrendOut(BaseModel):
    date: str
    refusals: int
    total_messages: int
    refusal_rate: float


class RefusalItemOut(BaseModel):
    id: int
    whatsapp_number: str
    user_message: str
    bot_response: str
    refusal_category: str | None
    created_at: datetime | None


class RefusalListResponse(BaseModel):
    items: list[RefusalItemOut]
    total: int
    page: int
    pages: int


# ── Endpoints ──

@router.get("/summary", response_model=RefusalSummaryOut)
def refusal_summary(
    db: Session = Depends(get_db),
    _admin: Any = Depends(require_staff),
    range_: tuple[datetime, datetime] = Depends(_parse_range),
) -> RefusalSummaryOut:
    start, end_exclusive = range_

    total_messages = int(
        db.query(func.count(ChatLog.id))
        .filter(ChatLog.created_at >= start, ChatLog.created_at < end_exclusive)
        .scalar() or 0
    )

    refusal_q = (
        db.query(
            func.count(ChatLog.id),
            func.count(func.distinct(ChatLog.whatsapp_number)),
        )
        .filter(
            ChatLog.created_at >= start,
            ChatLog.created_at < end_exclusive,
            ChatLog.response_type == "refused",
        )
        .one()
    )

    total_refusals = int(refusal_q[0] or 0)
    unique_users = int(refusal_q[1] or 0)

    return RefusalSummaryOut(
        total_refusals=total_refusals,
        total_messages=total_messages,
        refusal_rate=round(total_refusals / total_messages, 4) if total_messages > 0 else 0.0,
        unique_users_refused=unique_users,
    )


@router.get("/grouped", response_model=list[RefusalGroupedOut])
def refusal_grouped(
    db: Session = Depends(get_db),
    _admin: Any = Depends(require_staff),
    range_: tuple[datetime, datetime] = Depends(_parse_range),
) -> list[RefusalGroupedOut]:
    start, end_exclusive = range_

    rows = (
        db.query(
            func.coalesce(ChatLog.refusal_category, "uncategorized").label("cat"),
            func.count(ChatLog.id),
        )
        .filter(
            ChatLog.created_at >= start,
            ChatLog.created_at < end_exclusive,
            ChatLog.response_type == "refused",
        )
        .group_by("cat")
        .order_by(func.count(ChatLog.id).desc())
        .all()
    )

    total = sum(r[1] for r in rows)
    return [
        RefusalGroupedOut(
            category=str(r[0]),
            count=int(r[1]),
            percentage=round(r[1] / total * 100, 1) if total > 0 else 0.0,
        )
        for r in rows
    ]


@router.get("/trend", response_model=list[RefusalTrendOut])
def refusal_trend(
    db: Session = Depends(get_db),
    _admin: Any = Depends(require_staff),
    range_: tuple[datetime, datetime] = Depends(_parse_range),
) -> list[RefusalTrendOut]:
    start, end_exclusive = range_
    day_col = cast(ChatLog.created_at, Date)

    # Total messages per day
    total_rows = dict(
        db.query(day_col.label("d"), func.count(ChatLog.id))
        .filter(ChatLog.created_at >= start, ChatLog.created_at < end_exclusive)
        .group_by(day_col)
        .all()
    )

    # Refusals per day
    refusal_rows = dict(
        db.query(day_col.label("d"), func.count(ChatLog.id))
        .filter(
            ChatLog.created_at >= start,
            ChatLog.created_at < end_exclusive,
            ChatLog.response_type == "refused",
        )
        .group_by(day_col)
        .all()
    )

    # Merge into a complete timeline
    all_dates = sorted(set(list(total_rows.keys()) + list(refusal_rows.keys())))
    return [
        RefusalTrendOut(
            date=d.isoformat() if hasattr(d, "isoformat") else str(d),
            refusals=int(refusal_rows.get(d, 0)),
            total_messages=int(total_rows.get(d, 0)),
            refusal_rate=round(refusal_rows.get(d, 0) / total_rows[d], 4) if total_rows.get(d, 0) else 0.0,
        )
        for d in all_dates
    ]


@router.get("/list", response_model=RefusalListResponse)
def refusal_list(
    db: Session = Depends(get_db),
    _admin: Any = Depends(require_staff),
    range_: tuple[datetime, datetime] = Depends(_parse_range),
    category: Optional[str] = Query(None, description="Filter by refusal_category"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
) -> RefusalListResponse:
    start, end_exclusive = range_

    q = db.query(ChatLog).filter(
        ChatLog.created_at >= start,
        ChatLog.created_at < end_exclusive,
        ChatLog.response_type == "refused",
    )

    if category:
        if category == "uncategorized":
            q = q.filter(ChatLog.refusal_category.is_(None))
        else:
            q = q.filter(ChatLog.refusal_category == category)

    total = q.count()
    pages = max(1, (total + per_page - 1) // per_page)

    items = (
        q.order_by(desc(ChatLog.created_at))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return RefusalListResponse(
        items=[
            RefusalItemOut(
                id=item.id,
                whatsapp_number=item.whatsapp_number,
                user_message=item.user_message,
                bot_response=item.bot_response,
                refusal_category=item.refusal_category,
                created_at=item.created_at,
            )
            for item in items
        ],
        total=total,
        page=page,
        pages=pages,
    )
