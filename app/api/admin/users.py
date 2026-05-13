"""
Admin user-management endpoints.

All paths are relative to /admin/users (prefix set in __init__.py).

GET    /                       — paginated user list with search & filters
GET    /{whatsapp_number}      — single user detail + recent chat logs
PATCH  /{whatsapp_number}/plan — change plan name & credits
PATCH  /{whatsapp_number}/status — change status
PATCH  /{whatsapp_number}/dates  — edit subscription start/end
POST   /{whatsapp_number}/block   — block user
POST   /{whatsapp_number}/unblock — unblock user
POST   /{whatsapp_number}/send    — send a manual WhatsApp message
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, func, desc
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional

from app.db.connection import get_db
from app.db.models import Subscription, ChatLog, AuditEvent, AdminUser, UserProfile
from app.middleware.admin_auth import get_current_admin, require_admin_role
from app.api.whatsapp import send_whatsapp_message

router = APIRouter()


# ──────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────

class UserOut(BaseModel):
    id: int
    whatsapp_number: str
    status: str | None
    plan_name: str | None
    is_recurring: bool
    credits: int
    total_purchased: int
    message_count: int
    is_trial: bool
    subscription_start: datetime | None
    subscription_end: datetime | None
    created_at: datetime | None
    updated_at: datetime | None

    class Config:
        from_attributes = True


class UserListResponse(BaseModel):
    users: list[UserOut]
    total: int
    page: int
    pages: int


class ChatLogOut(BaseModel):
    id: int
    user_message: str
    bot_response: str
    response_type: str | None
    chunks_used: list | dict | None = None
    created_at: datetime | None

    class Config:
        from_attributes = True


class ProfileOut(BaseModel):
    weight_kg: float | None = None
    height_cm: float | None = None
    age: int | None = None
    goals: str | None = None
    sport: str | None = None
    dietary_preferences: str | None = None
    training_frequency: str | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class UserDetailResponse(BaseModel):
    user: UserOut
    profile: ProfileOut | None = None
    recent_chats: list[ChatLogOut]


class PlanUpdate(BaseModel):
    plan_name: str
    credits: int
    is_recurring: bool = False


class StatusUpdate(BaseModel):
    status: str  # active | inactive | expired | blocked


class DatesUpdate(BaseModel):
    subscription_start: datetime | None = None
    subscription_end: datetime | None = None


class SendMessageRequest(BaseModel):
    message: str


# ──────────────────────────────────
# Helpers
# ──────────────────────────────────

def _log_audit(
    db: Session,
    admin: AdminUser,
    action: str,
    target_id: str,
    details: dict | None = None,
):
    db.add(AuditEvent(
        actor_id=admin.id,
        actor_email=admin.email,
        action=action,
        target_type="user",
        target_id=target_id,
        details=details,
    ))


def _get_subscription_or_404(db: Session, whatsapp_number: str) -> Subscription:
    sub = db.query(Subscription).filter(
        Subscription.whatsapp_number == whatsapp_number
    ).first()
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {whatsapp_number} not found",
        )
    return sub


# ──────────────────────────────────
# Endpoints
# ──────────────────────────────────

@router.get("", response_model=UserListResponse)
def list_users(
    search: str = Query("", description="Search by phone number"),
    status_filter: str = Query("", alias="status", description="Filter: active, inactive, expired, blocked"),
    plan_filter: str = Query("", alias="plan", description="Filter by plan_name (partial match)"),
    is_trial: Optional[bool] = Query(None, description="Filter trial users"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Paginated user list with optional search and filters."""
    q = db.query(Subscription)

    if search:
        q = q.filter(Subscription.whatsapp_number.ilike(f"%{search}%"))
    if status_filter:
        q = q.filter(Subscription.status == status_filter)
    if plan_filter:
        q = q.filter(Subscription.plan_name.ilike(f"%{plan_filter}%"))
    if is_trial is not None:
        q = q.filter(Subscription.is_trial == is_trial)

    total = q.count()
    pages = max(1, (total + per_page - 1) // per_page)

    users = (
        q.order_by(desc(Subscription.created_at))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return UserListResponse(
        users=[UserOut.model_validate(u) for u in users],
        total=total,
        page=page,
        pages=pages,
    )


@router.get("/{whatsapp_number}", response_model=UserDetailResponse)
def get_user_detail(
    whatsapp_number: str,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Single user detail with their last 20 chat logs and profile."""
    sub = _get_subscription_or_404(db, whatsapp_number)

    profile = db.query(UserProfile).filter_by(whatsapp_number=whatsapp_number).first()

    recent_chats = (
        db.query(ChatLog)
        .filter(ChatLog.whatsapp_number == whatsapp_number)
        .order_by(desc(ChatLog.created_at))
        .limit(20)
        .all()
    )

    return UserDetailResponse(
        user=UserOut.model_validate(sub),
        profile=ProfileOut.model_validate(profile) if profile else None,
        recent_chats=[ChatLogOut.model_validate(c) for c in recent_chats],
    )


@router.patch("/{whatsapp_number}/plan", response_model=UserOut)
def update_plan(
    whatsapp_number: str,
    body: PlanUpdate,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Change a user's plan, credits, and recurring flag. Admin and support."""
    sub = _get_subscription_or_404(db, whatsapp_number)

    old_plan = sub.plan_name
    old_credits = sub.credits

    sub.plan_name = body.plan_name
    sub.credits = body.credits
    sub.is_recurring = body.is_recurring
    sub.is_trial = False

    _log_audit(db, admin, "PLAN_CHANGE", whatsapp_number, {
        "from_plan": old_plan,
        "to_plan": body.plan_name,
        "from_credits": old_credits,
        "to_credits": body.credits,
    })
    db.commit()
    db.refresh(sub)

    return UserOut.model_validate(sub)


@router.patch("/{whatsapp_number}/status", response_model=UserOut)
def update_status(
    whatsapp_number: str,
    body: StatusUpdate,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin_role),
):
    """Change a user's status. Admin only."""
    allowed = {"active", "inactive", "expired", "blocked"}
    if body.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Status must be one of: {', '.join(sorted(allowed))}",
        )

    sub = _get_subscription_or_404(db, whatsapp_number)
    old_status = sub.status
    sub.status = body.status

    _log_audit(db, admin, "STATUS_CHANGE", whatsapp_number, {
        "from": old_status,
        "to": body.status,
    })
    db.commit()
    db.refresh(sub)

    return UserOut.model_validate(sub)


@router.patch("/{whatsapp_number}/dates", response_model=UserOut)
def update_dates(
    whatsapp_number: str,
    body: DatesUpdate,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin_role),
):
    """Edit subscription start/end dates. Admin only."""
    sub = _get_subscription_or_404(db, whatsapp_number)

    old_start = str(sub.subscription_start)
    old_end = str(sub.subscription_end)

    if body.subscription_start is not None:
        sub.subscription_start = body.subscription_start
    if body.subscription_end is not None:
        sub.subscription_end = body.subscription_end

    _log_audit(db, admin, "DATES_CHANGE", whatsapp_number, {
        "from_start": old_start,
        "to_start": str(sub.subscription_start),
        "from_end": old_end,
        "to_end": str(sub.subscription_end),
    })
    db.commit()
    db.refresh(sub)

    return UserOut.model_validate(sub)


@router.post("/{whatsapp_number}/block", response_model=UserOut)
def block_user(
    whatsapp_number: str,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin_role),
):
    """Block a user. Admin only."""
    sub = _get_subscription_or_404(db, whatsapp_number)

    if sub.status == "blocked":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already blocked",
        )

    old_status = sub.status
    sub.status = "blocked"

    _log_audit(db, admin, "BLOCK", whatsapp_number, {
        "previous_status": old_status,
    })
    db.commit()
    db.refresh(sub)

    return UserOut.model_validate(sub)


@router.post("/{whatsapp_number}/unblock", response_model=UserOut)
def unblock_user(
    whatsapp_number: str,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin_role),
):
    """Unblock a user (sets status back to active). Admin only."""
    sub = _get_subscription_or_404(db, whatsapp_number)

    if sub.status != "blocked":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not blocked",
        )

    sub.status = "active"

    _log_audit(db, admin, "UNBLOCK", whatsapp_number, {
        "restored_status": "active",
    })
    db.commit()
    db.refresh(sub)

    return UserOut.model_validate(sub)


@router.post("/{whatsapp_number}/send", status_code=status.HTTP_200_OK)
async def send_message_to_user(
    whatsapp_number: str,
    body: SendMessageRequest,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Send a manual WhatsApp message to a user. Both admin and support can use this."""
    # Verify user exists
    _get_subscription_or_404(db, whatsapp_number)

    # Send via Meta API (reuse existing helper)
    await send_whatsapp_message(whatsapp_number, body.message)

    _log_audit(db, admin, "SEND_MESSAGE", whatsapp_number, {
        "message_preview": body.message[:100],
    })
    db.commit()
    return {"status": "sent", "to": whatsapp_number}
