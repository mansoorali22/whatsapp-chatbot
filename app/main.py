from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware # 1. Import the middleware
from sqlalchemy.orm import Session
from app.db.connection import init_db, get_db
from app.utils.logger import setup_logging
from app.db.models import ProcessedMessage, Subscription
from app.api import whatsapp, plugnpay

from contextlib import asynccontextmanager
from app.services.rag import init_rag_components # Import your loader

@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP LOGIC
    setup_logging()
    init_db()
    init_rag_components() # Initialize LLM and VectorStore here
    print("🚀 Service Started: Atleet Buddy AI")
    yield
    # SHUTDOWN LOGIC (Optional: close DB pools)
    print("🛑 Service Stopping...")

app = FastAPI(title="Atleet Buddy AI", lifespan=lifespan)


origins = [
    "http://localhost:3000",
    "*",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # Allows all HTTP methods (GET, POST, etc.)
    allow_headers=["*"], # Allows all headers
)

app.include_router(whatsapp.router, prefix="/whatsapp", tags=["WhatsApp"])
app.include_router(plugnpay.router, prefix="/plugpay", tags=["Plug&Pay"])

@app.get("/")
def root():
    """Root endpoint - confirms app is running"""
    return {
        "status": "online",
        "service": "Atleet Buddy AI",
        "message": "WhatsApp chatbot is running",
        "endpoints": {
            "docs": "/docs",
            "whatsapp_webhook": "/whatsapp/webhook",
            "plugpay_webhook": "/plugpay/webhook"
        }
    }

@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "Atleet Buddy AI"}
