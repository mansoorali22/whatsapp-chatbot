"""
Admin API router — serves the Atleet Buddy dashboard.

All endpoints are prefixed with /admin (set in main.py).
"""

from fastapi import APIRouter
from .auth import router as auth_router
from .users import router as users_router
from .audit import router as audit_router
from .usage import router as usage_router
from .refusals import router as refusals_router
from .alerts import router as alerts_router

router = APIRouter()

# /admin/auth/login, /admin/auth/me
router.include_router(auth_router, prefix="/auth", tags=["Admin Auth"])

# /admin/users, /admin/users/{whatsapp_number}, /admin/users/{whatsapp_number}/plan, etc.
router.include_router(users_router, prefix="/users", tags=["Admin Users"])

# /admin/audit, /admin/audit/export
router.include_router(audit_router, prefix="/audit", tags=["Admin Audit"])

# /admin/usage/summary, /admin/usage/daily, /admin/usage/per-user, /admin/usage/dashboard
router.include_router(usage_router, tags=["Admin Usage"])

# /admin/refusals/summary, /admin/refusals/grouped, /admin/refusals/trend, /admin/refusals/list
router.include_router(refusals_router, tags=["Admin Refusals"])

# /admin/alerts, /admin/alerts/stats, /admin/alerts/{id}
router.include_router(alerts_router, tags=["Admin Alerts"])
