"""
B3 — Admin usage & cost analytics (read-only).

Endpoints (all require admin or support JWT):
  GET /admin/usage/summary?from=YYYY-MM-DD&to=YYYY-MM-DD
  GET /admin/usage/daily?from=...&to=...
  GET /admin/usage/per-user?from=...&to=...
  GET /admin/usage/dashboard   (no params)

Requires on ChatLog: prompt_tokens, completion_tokens, cost_usd, model (nullable),
response_type, created_at, whatsapp_number.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Date, cast, distinct, func
from sqlalchemy.orm import Session

from app.db.connection import get_db
from app.db.models import ChatLog, Subscription
from app.middleware.admin_auth import get_current_admin

router = APIRouter(tags=["Admin Usage"])


def require_staff(admin: Any = Depends(get_current_admin)) -> Any:
    if getattr(admin, "role", None) not in ("admin", "support"):
        raise HTTPException(status_code=403, detail="Staff role required")
    return admin


def _parse_range(
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = Query(None),
) -> tuple[datetime, datetime]:
    """Inclusive calendar dates in UTC → [start, end_exclusive)."""
    if from_ is None or to is None:
        raise HTTPException(status_code=400, detail="Query params 'from' and 'to' are required (YYYY-MM-DD)")
    if to < from_:
        raise HTTPException(status_code=400, detail="'to' must be on or after 'from'")
    start = datetime(from_.year, from_.month, from_.day, tzinfo=timezone.utc)
    end_day = datetime(to.year, to.month, to.day, tzinfo=timezone.utc)
    end_exclusive = end_day + timedelta(days=1)
    return start, end_exclusive


class UsageSummaryOut(BaseModel):
    total_messages: int
    active_users: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost: float


class DailyUsageOut(BaseModel):
    date: str
    messages: int
    prompt_tokens: int
    completion_tokens: int
    cost: float


class PerUserUsageOut(BaseModel):
    whatsapp_number: str
    messages: int
    prompt_tokens: int
    completion_tokens: int
    cost: float


class DashboardStatsOut(BaseModel):
    total_users: int
    active_subscriptions: int
    messages_today: int
    cost_today_usd: float
    messages_yesterday: int
    cost_yesterday_usd: float


@router.get("/usage/summary", response_model=UsageSummaryOut)
def usage_summary(
    db: Session = Depends(get_db),
    _admin: Any = Depends(require_staff),
    range_: tuple[datetime, datetime] = Depends(_parse_range),
) -> UsageSummaryOut:
    start, end_exclusive = range_
    q = (
        db.query(
            func.count(ChatLog.id),
            func.count(distinct(ChatLog.whatsapp_number)),
            func.coalesce(func.sum(ChatLog.prompt_tokens), 0),
            func.coalesce(func.sum(ChatLog.completion_tokens), 0),
            func.coalesce(func.sum(ChatLog.cost_usd), 0.0),
        )
        .filter(
            ChatLog.created_at >= start,
            ChatLog.created_at < end_exclusive,
            ChatLog.response_type == "answered",
        )
        .one()
    )
    return UsageSummaryOut(
        total_messages=int(q[0] or 0),
        active_users=int(q[1] or 0),
        total_prompt_tokens=int(q[2] or 0),
        total_completion_tokens=int(q[3] or 0),
        total_cost=float(q[4] or 0),
    )


@router.get("/usage/daily", response_model=list[DailyUsageOut])
def usage_daily(
    db: Session = Depends(get_db),
    _admin: Any = Depends(require_staff),
    range_: tuple[datetime, datetime] = Depends(_parse_range),
) -> list[DailyUsageOut]:
    start, end_exclusive = range_
    day_col = cast(ChatLog.created_at, Date)
    rows = (
        db.query(
            day_col.label("d"),
            func.count(ChatLog.id),
            func.coalesce(func.sum(ChatLog.prompt_tokens), 0),
            func.coalesce(func.sum(ChatLog.completion_tokens), 0),
            func.coalesce(func.sum(ChatLog.cost_usd), 0.0),
        )
        .filter(
            ChatLog.created_at >= start,
            ChatLog.created_at < end_exclusive,
            ChatLog.response_type == "answered",
        )
        .group_by(day_col)
        .order_by(day_col)
        .all()
    )
    return [
        DailyUsageOut(
            date=r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
            messages=int(r[1] or 0),
            prompt_tokens=int(r[2] or 0),
            completion_tokens=int(r[3] or 0),
            cost=float(r[4] or 0),
        )
        for r in rows
    ]


@router.get("/usage/per-user", response_model=list[PerUserUsageOut])
def usage_per_user(
    db: Session = Depends(get_db),
    _admin: Any = Depends(require_staff),
    range_: tuple[datetime, datetime] = Depends(_parse_range),
) -> list[PerUserUsageOut]:
    start, end_exclusive = range_
    rows = (
        db.query(
            ChatLog.whatsapp_number,
            func.count(ChatLog.id),
            func.coalesce(func.sum(ChatLog.prompt_tokens), 0),
            func.coalesce(func.sum(ChatLog.completion_tokens), 0),
            func.coalesce(func.sum(ChatLog.cost_usd), 0.0),
        )
        .filter(
            ChatLog.created_at >= start,
            ChatLog.created_at < end_exclusive,
            ChatLog.response_type == "answered",
        )
        .group_by(ChatLog.whatsapp_number)
        .order_by(func.count(ChatLog.id).desc())
        .all()
    )
    return [
        PerUserUsageOut(
            whatsapp_number=str(r[0]),
            messages=int(r[1] or 0),
            prompt_tokens=int(r[2] or 0),
            completion_tokens=int(r[3] or 0),
            cost=float(r[4] or 0),
        )
        for r in rows
    ]


def _day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


@router.get("/usage/dashboard", response_model=DashboardStatsOut)
def usage_dashboard(
    db: Session = Depends(get_db),
    _admin: Any = Depends(require_staff),
) -> DashboardStatsOut:
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)

    total_users = int(db.query(func.count(Subscription.id)).scalar() or 0)
    active_subs = int(
        db.query(func.count(Subscription.id))
        .filter(Subscription.status == "active", Subscription.is_trial.is_(False))
        .scalar()
        or 0
    )

    t0, t1 = _day_bounds_utc(today)
    y0, y1 = _day_bounds_utc(yesterday)

    def _msgs_cost(start: datetime, end: datetime) -> tuple[int, float]:
        row = (
            db.query(
                func.count(ChatLog.id),
                func.coalesce(func.sum(ChatLog.cost_usd), 0.0),
            )
            .filter(
                ChatLog.created_at >= start,
                ChatLog.created_at < end,
                ChatLog.response_type == "answered",
            )
            .one()
        )
        return int(row[0] or 0), float(row[1] or 0)

    msg_today, cost_today = _msgs_cost(t0, t1)
    msg_yest, cost_yest = _msgs_cost(y0, y1)

    return DashboardStatsOut(
        total_users=total_users,
        active_subscriptions=active_subs,
        messages_today=msg_today,
        cost_today_usd=cost_today,
        messages_yesterday=msg_yest,
        cost_yesterday_usd=cost_yest,
    )
