"""
Admin authentication endpoints.

POST /admin/auth/login  — email + password → JWT
GET  /admin/auth/me     — JWT → current admin user info
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
import bcrypt
import jwt

from app.core.config import settings
from app.db.connection import get_db
from app.db.models import AdminUser, AuditEvent
from app.middleware.admin_auth import get_current_admin

router = APIRouter()


# ──────────────────────────────────
# Request / Response schemas
# ──────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    admin: "AdminInfo"


class AdminInfo(BaseModel):
    id: int
    email: str
    display_name: str | None
    role: str

    class Config:
        from_attributes = True


# ──────────────────────────────────
# Endpoints
# ──────────────────────────────────

@router.post("/login", response_model=LoginResponse)
def admin_login(body: LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticate an admin user with email + password.
    Returns a JWT that the dashboard stores and sends on every request.
    """
    # 1. Find admin by email
    admin = db.query(AdminUser).filter(AdminUser.email == body.email).first()
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # 2. Verify password with bcrypt
    if not bcrypt.checkpw(
        body.password.encode("utf-8"),
        admin.password_hash.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # 3. Issue JWT
    now = datetime.now(timezone.utc)
    payload = {
        "admin_id": admin.id,
        "email": admin.email,
        "role": admin.role,
        "iat": now,
        "exp": now + timedelta(hours=settings.ADMIN_JWT_EXPIRY_HOURS),
    }
    token = jwt.encode(payload, settings.ADMIN_JWT_SECRET, algorithm="HS256")

    # 4. Update last_login_at
    admin.last_login_at = now
    db.commit()

    # 5. Log the login in audit trail
    db.add(AuditEvent(
        actor_id=admin.id,
        actor_email=admin.email,
        action="LOGIN",
        target_type="admin",
        target_id=str(admin.id),
    ))
    db.commit()

    return LoginResponse(
        access_token=token,
        admin=AdminInfo(
            id=admin.id,
            email=admin.email,
            display_name=admin.display_name,
            role=admin.role,
        ),
    )


@router.get("/me", response_model=AdminInfo)
def get_me(admin: AdminUser = Depends(get_current_admin)):
    """
    Return the currently authenticated admin's info.
    The dashboard calls this on page load to verify the token is still valid.
    """
    return AdminInfo(
        id=admin.id,
        email=admin.email,
        display_name=admin.display_name,
        role=admin.role,
    )
