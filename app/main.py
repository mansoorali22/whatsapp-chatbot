# Standard library imports
from contextlib import asynccontextmanager

# FastAPI imports
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware

# SQLAlchemy imports
from sqlalchemy.orm import Session

# APScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

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


# Build CORS origins: always include local dev, plus dashboard origins from env
_dashboard_origins = [
    o.strip() for o in settings.DASHBOARD_ORIGINS.split(",") if o.strip()
]
origins = [
    "http://localhost:3000",
    *_dashboard_origins,
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Allows all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers (incl. Authorization)
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
    """Health check endpoint. HEAD allowed for Render health checks."""
    return {"status": "healthy", "service": "Atleet Buddy AI"}
