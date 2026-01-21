"""
FastAPI application entry point
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging

from app.config import settings
from app.logger import setup_logging
from db.connection import init_db, close_db, check_db_health
from app.routes import whatsapp, plugnpay

# Setup logging first
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for startup and shutdown"""
    # Startup
    logger.info("Starting WhatsApp AI Book Chatbot...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Book: {settings.BOOK_TITLE}")
    
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("Shutting down application...")
    await close_db()
    logger.info("Application shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="WhatsApp AI Book Chatbot",
    description="Mode A: Strict book-only chatbot with citations",
    version="1.0.0-mvp",
    lifespan=lifespan
)


# Include routers
app.include_router(whatsapp.router, prefix="/webhook", tags=["WhatsApp"])
app.include_router(plugnpay.router, prefix="/plugnpay", tags=["Plug & Pay"])


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "WhatsApp AI Book Chatbot",
        "version": "1.0.0-mvp",
        "mode": "A - Strict book-only",
        "book": settings.BOOK_TITLE,
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    db_healthy = await check_db_health()
    
    health_status = {
        "status": "healthy" if db_healthy else "unhealthy",
        "database": "connected" if db_healthy else "disconnected",
        "environment": settings.ENVIRONMENT
    }
    
    status_code = 200 if db_healthy else 503
    return JSONResponse(content=health_status, status_code=status_code)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": str(exc) if settings.ENVIRONMENT == "development" else "An error occurred"
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.ENVIRONMENT == "development"
    )