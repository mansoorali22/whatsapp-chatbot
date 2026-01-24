from fastapi import FastAPI, Depends, Request
from sqlalchemy.orm import Session
from app.db.connection import init_db, get_db
from app.db.models import ProcessedMessage, Subscription


app = FastAPI(title="Atleet Buddy AI")

@app.on_event("startup")
def on_startup():
    init_db()
