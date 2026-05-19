# Standard library imports
from contextlib import asynccontextmanager
import traceback

# FastAPI imports
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# SQLAlchemy imports
from sqlalchemy.orm import Session

# APScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# App imports
from app.db.connection import init_db, get_db, SessionLocal
from app.db.models import ProcessedMessage, Subscription
from app.utils.logger import setup_logging
from app.utils.cleanup import run_processed_message_cleanup
from app.api import whatsapp, plugnpay
from app.api.admin import router as admin_router
from app.services.rag import init_rag_components
from app.services.alert_checks import run_alert_checks
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP LOGIC
    setup_logging()
    init_db()
    init_rag_components() # Initialize LLM and VectorStore here
    scheduler.add_job(
        run_processed_message_cleanup,
        CronTrigger(hour=0, minute=0),
        id="midnight_cleanup",
        replace_existing=True
    )
    scheduler.add_job(
        run_alert_checks,
        CronTrigger(minute="*/30"),  # Every 30 minutes
        id="alert_checks",
        replace_existing=True
    )

    scheduler.start()
    print("Service Started: Atleet Buddy AI (Scheduler Running too)")

    yield
    # SHUTDOWN LOGIC (Optional: close DB pools)
    scheduler.shutdown() # Stop the scheduler when the app closes
    print("Service Stopping...")

app = FastAPI(title="Atleet Buddy AI", lifespan=lifespan)
scheduler = BackgroundScheduler()

# D/E7: Rate limiting — protects webhooks and admin API from abuse
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Build CORS origins: always include local dev, plus dashboard origins from env
_dashboard_origins = [
    o.strip() for o in settings.DASHBOARD_ORIGINS.split(",") if o.strip()
]
origins = [
    "http://localhost:3000",
    *_dashboard_origins,
]

# Also allow any Vercel preview deployment for the dashboard project
_allow_origin_regex = r"https://atleet-buddy-hub.*\.vercel\.app"

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled errors so CORS headers are still included and
    the real error shows in Render logs instead of a bare 500."""
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )

app.include_router(whatsapp.router, prefix="/whatsapp", tags=["WhatsApp"])
app.include_router(plugnpay.router, prefix="/plugpay", tags=["Plug&Pay"])
app.include_router(admin_router, prefix="/admin", tags=["Admin Dashboard"])

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    """Root endpoint - confirms app is running. HEAD allowed for Render health checks."""
    return {
        "status": "online",
        "service": "Atleet Buddy AI",
        "message": "WhatsApp chatbot is running",
        "endpoints": {
            "docs": "/docs",
            "whatsapp_webhook": "/whatsapp/get-messages",
            "plugpay_webhook": "/plugpay/webhook"
        }
    }

@app.api_route("/health", methods=["GET", "HEAD"])
def health_check():
    """Health check endpoint with DB connectivity verification. HEAD allowed for Render health checks."""
    from sqlalchemy import text
    db_status = "ok"
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
    except Exception:
        db_status = "unavailable"
    status = "healthy" if db_status == "ok" else "degraded"
    result = {"status": status, "service": "Atleet Buddy AI", "database": db_status}
    if db_status != "ok":
        return JSONResponse(content=result, status_code=503)
    return result
