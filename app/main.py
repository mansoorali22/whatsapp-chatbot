from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware # 1. Import the middleware
from sqlalchemy.orm import Session
from app.db.connection import init_db, get_db
from app.utils.logger import setup_logging
from app.db.models import ProcessedMessage, Subscription
from app.api import whatsapp, plugnpay

app = FastAPI(title="Atleet Buddy AI")

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

@app.on_event("startup")
def on_startup():
    setup_logging()
    init_db()