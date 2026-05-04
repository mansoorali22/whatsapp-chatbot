"""
JWT authentication middleware for admin dashboard endpoints.

Usage in routes:
    from app.middleware.admin_auth import get_current_admin, require_admin_role

    @router.get("/protected")
    def protected_route(admin: AdminUser = Depends(get_current_admin)):
        ...

    @router.get("/admin-only")
    def admin_only_route(admin: AdminUser = Depends(require_admin_role)):
        ...
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
import jwt

from app.core.config import settings
from app.db.connection import get_db
from app.db.models import AdminUser

# HTTPBearer extracts "Bearer <token>" from Authorization header
security = HTTPBearer()


def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> AdminUser:
    """
    Dependency that:
    1. Extracts JWT from Authorization: Bearer <token>
    2. Decodes and verifies the token
    3. Loads the AdminUser from DB
    4. Returns the AdminUser or raises 401
    """
    token = credentials.credentials

    try:
        payload = jwt.decode(
            token,
            settings.ADMIN_JWT_SECRET,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    admin_id: int = payload.get("admin_id")
    if admin_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    admin = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin user not found",
        )

    return admin


def require_admin_role(
    admin: AdminUser = Depends(get_current_admin),
) -> AdminUser:
    """
    Dependency that ensures the current admin has role='admin' (not 'support').
    Use for destructive or sensitive operations.
    """
    if admin.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return admin
