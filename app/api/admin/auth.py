"""
Admin authentication endpoints.

POST /admin/auth/login              - email + password -> JWT
GET  /admin/auth/me                 - JWT -> current admin user info
POST /admin/auth/change-password    - change own password
GET  /admin/auth/support-accounts   - list support accounts (admin only)
POST /admin/auth/support-accounts   - create support account (admin only)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
import jwt

from app.core.config import settings
from app.db.connection import get_db
from app.db.models import AdminUser, AuditEvent
from app.middleware.admin_auth import get_current_admin, require_admin_role

router = APIRouter()


# --- Request / Response schemas ---

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


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class CreateSupportAccountRequest(BaseModel):
    email: str
    display_name: str
    password: str


class SupportAccountOut(BaseModel):
    id: int
    email: str
    display_name: str | None
    role: str
    created_at: datetime | None

    class Config:
        from_attributes = True


# --- Endpoints ---

@router.post("/login", response_model=LoginResponse)
def admin_login(body: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate an admin user with email + password. Returns a JWT."""
    admin = db.query(AdminUser).filter(AdminUser.email == body.email).first()
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not bcrypt.checkpw(
        body.password.encode("utf-8"),
        admin.password_hash.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    now = datetime.now(timezone.utc)
    payload = {
        "admin_id": admin.id,
        "email": admin.email,
        "role": admin.role,
        "iat": now,
        "exp": now + timedelta(hours=settings.ADMIN_JWT_EXPIRY_HOURS),
    }
    token = jwt.encode(payload, settings.ADMIN_JWT_SECRET, algorithm="HS256")

    admin.last_login_at = now
    db.commit()

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
    """Return the currently authenticated admin's info."""
    return AdminInfo(
        id=admin.id,
        email=admin.email,
        display_name=admin.display_name,
        role=admin.role,
    )


# --- Password change ---

@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Change the currently authenticated admin's password."""
    if not bcrypt.checkpw(
        body.current_password.encode("utf-8"),
        admin.password_hash.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 8 characters",
        )

    new_hash = bcrypt.hashpw(
        body.new_password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")
    admin.password_hash = new_hash

    db.add(AuditEvent(
        actor_id=admin.id,
        actor_email=admin.email,
        action="PASSWORD_CHANGE",
        target_type="admin",
        target_id=str(admin.id),
    ))
    db.commit()

    return {"message": "Password changed successfully"}


# --- Support account management (admin only) ---

@router.get("/support-accounts", response_model=list[SupportAccountOut])
def list_support_accounts(
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin_role),
):
    """List all support accounts. Admin only."""
    accounts = (
        db.query(AdminUser)
        .filter(AdminUser.role == "support")
        .order_by(AdminUser.created_at.desc())
        .all()
    )
    return [SupportAccountOut.model_validate(a) for a in accounts]


@router.post("/support-accounts", response_model=SupportAccountOut, status_code=status.HTTP_201_CREATED)
def create_support_account(
    body: CreateSupportAccountRequest,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin_role),
):
    """Create a new support account. Admin only."""
    email = body.email.strip().lower()

    if len(body.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )

    existing = db.query(AdminUser).filter(AdminUser.email == email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account with email {email} already exists",
        )

    password_hash = bcrypt.hashpw(
        body.password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    new_account = AdminUser(
        email=email,
        display_name=body.display_name.strip(),
        password_hash=password_hash,
        role="support",
    )
    db.add(new_account)

    db.add(AuditEvent(
        actor_id=admin.id,
        actor_email=admin.email,
        action="CREATE_SUPPORT_ACCOUNT",
        target_type="admin",
        target_id=email,
        details={"display_name": body.display_name.strip()},
    ))
    db.commit()
    db.refresh(new_account)

    return SupportAccountOut.model_validate(new_account)
