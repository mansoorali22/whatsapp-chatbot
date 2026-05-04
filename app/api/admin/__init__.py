"""
Admin API router — serves the Atleet Buddy dashboard.

All endpoints are prefixed with /admin (set in main.py).
"""

from fastapi import APIRouter
from .auth import router as auth_router
from .users import router as users_router
from .audit import router as audit_router

router = APIRouter()

# /admin/auth/login, /admin/auth/me
router.include_router(auth_router, prefix="/auth", tags=["Admin Auth"])

# /admin/users, /admin/users/{whatsapp_number}, /admin/users/{whatsapp_number}/plan, etc.
router.include_router(users_router, prefix="/users", tags=["Admin Users"])

# /admin/audit
router.include_router(audit_router, prefix="/audit", tags=["Admin Audit"])
