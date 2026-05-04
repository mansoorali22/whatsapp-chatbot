"""
B6 — Alerts system.

Endpoints (all require admin or support JWT):
  GET  /admin/alerts          — list alerts (active by default, filterable)
  GET  /admin/alerts/stats    — quick count of active alerts by severity
  PATCH /admin/alerts/{id}    — acknowledge or resolve an alert
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.db.connection import get_db
from app.db.models import Alert, AdminUser
from app.middleware.admin_auth import get_current_admin

router = APIRouter(prefix="/alerts", tags=["Admin Alerts"])


def require_staff(admin: Any = Depends(get_current_admin)) -> Any:
    if getattr(admin, "role", None) not in ("admin", "support"):
        raise HTTPException(status_code=403, detail="Staff role required")
    return admin


# ── Schemas ──

class AlertOut(BaseModel):
    id: int
    alert_type: str
    severity: str
    title: str
    message: str
    status: str
    acknowledged_by: int | None
    acknowledged_at: datetime | None
    details: dict | list | None = None
    created_at: datetime | None

    class Config:
        from_attributes = True


class AlertStatsOut(BaseModel):
    active_info: int
    active_warning: int
    active_critical: int
    total_active: int


class AlertListResponse(BaseModel):
    alerts: list[AlertOut]
    total: int
    page: int
    pages: int


class AlertPatchIn(BaseModel):
    status: str  # 'acknowledged' or 'resolved'


# ── Endpoints ──

@router.get("", response_model=AlertListResponse)
def list_alerts(
    status: Optional[str] = Query("active", description="Filter by status: active, acknowledged, resolved, all"),
    severity: Optional[str] = Query(None, description="Filter by severity: info, warning, critical"),
    alert_type: Optional[str] = Query(None, description="Filter by alert type"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: Any = Depends(require_staff),
) -> AlertListResponse:
    q = db.query(Alert)

    if status and status != "all":
        q = q.filter(Alert.status == status)
    if severity:
        q = q.filter(Alert.severity == severity)
    if alert_type:
        q = q.filter(Alert.alert_type == alert_type)

    total = q.count()
    pages = max(1, (total + per_page - 1) // per_page)

    alerts = (
        q.order_by(desc(Alert.created_at))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return AlertListResponse(
        alerts=[AlertOut.model_validate(a) for a in alerts],
        total=total,
        page=page,
        pages=pages,
    )


@router.get("/stats", response_model=AlertStatsOut)
def alert_stats(
    db: Session = Depends(get_db),
    _admin: Any = Depends(require_staff),
) -> AlertStatsOut:
    rows = (
        db.query(Alert.severity, func.count(Alert.id))
        .filter(Alert.status == "active")
        .group_by(Alert.severity)
        .all()
    )
    counts = {r[0]: r[1] for r in rows}
    info = counts.get("info", 0)
    warning = counts.get("warning", 0)
    critical = counts.get("critical", 0)
    return AlertStatsOut(
        active_info=info,
        active_warning=warning,
        active_critical=critical,
        total_active=info + warning + critical,
    )


@router.patch("/{alert_id}", response_model=AlertOut)
def patch_alert(
    alert_id: int = Path(...),
    body: AlertPatchIn = ...,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_staff),
) -> AlertOut:
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if body.status not in ("acknowledged", "resolved"):
        raise HTTPException(status_code=400, detail="status must be 'acknowledged' or 'resolved'")

    alert.status = body.status
    if body.status == "acknowledged":
        alert.acknowledged_by = admin.id
        alert.acknowledged_at = datetime.now(timezone.utc)
    elif body.status == "resolved":
        # Keep acknowledgment info, just update status
        pass

    db.commit()
    db.refresh(alert)
    return AlertOut.model_validate(alert)
