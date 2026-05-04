"""
Audit log endpoint.

GET /admin/audit — paginated audit events.
GET /admin/audit/export — CSV export of filtered audit events.

Role enforcement:
  - admin  → sees ALL events, can filter by any actor_email.
  - support → ONLY sees events where actor_id matches their own JWT,
              regardless of what the client sends as actor_email.
"""

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from app.db.connection import get_db
from app.db.models import AuditEvent, AdminUser
from app.middleware.admin_auth import get_current_admin

router = APIRouter()


# ──────────────────────────────────
# Schemas
# ──────────────────────────────────

class AuditEventOut(BaseModel):
    id: int
    actor_id: int | None
    actor_email: str
    action: str
    target_type: str | None
    target_id: str | None
    details: dict | list | None = None
    created_at: datetime | None

    class Config:
        from_attributes = True


class AuditListResponse(BaseModel):
    events: list[AuditEventOut]
    total: int
    page: int
    pages: int


# ──────────────────────────────────
# Endpoint
# ──────────────────────────────────

@router.get("", response_model=AuditListResponse)
def list_audit_events(
    actor_email: Optional[str] = Query(None, description="Filter by actor email (admin only; ignored for support)"),
    action: Optional[str] = Query(None, description="Filter by action type"),
    target_id: Optional[str] = Query(None, description="Filter by target (e.g. whatsapp_number)"),
    date_from: Optional[datetime] = Query(None, alias="from", description="Start of date range"),
    date_to: Optional[datetime] = Query(None, alias="to", description="End of date range"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """
    Paginated audit log.

    Support users are locked to their own events — the server ignores
    any actor_email the client sends and forces actor_id == their JWT id.
    Admins can filter freely or see everything.
    """
    q = db.query(AuditEvent)

    # ── Role-based enforcement ──
    if admin.role != "admin":
        # Support: ONLY their own events, regardless of query params
        q = q.filter(AuditEvent.actor_id == admin.id)
    elif actor_email:
        # Admin with an explicit filter
        q = q.filter(AuditEvent.actor_email == actor_email)

    # ── Optional filters (both roles) ──
    if action:
        q = q.filter(AuditEvent.action == action)
    if target_id:
        q = q.filter(AuditEvent.target_id == target_id)
    if date_from:
        q = q.filter(AuditEvent.created_at >= date_from)
    if date_to:
        q = q.filter(AuditEvent.created_at <= date_to)

    total = q.count()
    pages = max(1, (total + per_page - 1) // per_page)

    events = (
        q.order_by(desc(AuditEvent.created_at))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return AuditListResponse(
        events=[AuditEventOut.model_validate(e) for e in events],
        total=total,
        page=page,
        pages=pages,
    )


# ──────────────────────────────────
# CSV Export
# ──────────────────────────────────

@router.get("/export")
def export_audit_csv(
    actor_email: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    target_id: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """
    Export filtered audit events as CSV (max 10 000 rows).
    Same role enforcement as the list endpoint.
    """
    q = db.query(AuditEvent)

    if admin.role != "admin":
        q = q.filter(AuditEvent.actor_id == admin.id)
    elif actor_email:
        q = q.filter(AuditEvent.actor_email == actor_email)

    if action:
        q = q.filter(AuditEvent.action == action)
    if target_id:
        q = q.filter(AuditEvent.target_id == target_id)
    if date_from:
        q = q.filter(AuditEvent.created_at >= date_from)
    if date_to:
        q = q.filter(AuditEvent.created_at <= date_to)

    events = q.order_by(desc(AuditEvent.created_at)).limit(10_000).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "actor_email", "action", "target_type", "target_id", "details", "created_at"])
    for e in events:
        writer.writerow([
            e.id,
            e.actor_email,
            e.action,
            e.target_type or "",
            e.target_id or "",
            str(e.details) if e.details else "",
            e.created_at.isoformat() if e.created_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_export.csv"},
    )
