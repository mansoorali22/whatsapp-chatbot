from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Convert postgresql:// to postgresql+psycopg:// to use psycopg v3
# SQLAlchemy needs explicit dialect specification for psycopg v3
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    # Check if psycopg (v3) is available
    try:
        import psycopg
        # Replace postgresql:// with postgresql+psycopg://
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    except ImportError:
        # Fallback: try psycopg2 if psycopg is not available
        pass

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