from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# SQLAlchemy 2.0 will auto-detect psycopg (v3) if installed
# If using psycopg v3, the connection string can stay as postgresql://
# SQLAlchemy will prefer psycopg over psycopg2 if both are available
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

def init_db():
    # 1. Import models here so Base knows they exist before create_all
    from app.db import models 
    
    with engine.begin() as conn:
        # 2. Crucial for pgvector (Neon supports this)
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        print("✅ Vector extension verified/created.")

    # 3. Create all tables defined in models.py
    # Since 'models' was imported above, Base.metadata now contains your tables
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created successfully.")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()