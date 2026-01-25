from sqlalchemy import Column, String, Integer, DateTime, Text, JSON, Index, Boolean
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from .connection import Base

from sqlalchemy import Column, String, Integer, DateTime, Boolean, func
from .connection import Base

class Subscription(Base):
    __tablename__ = "subscriptions"
    
    id = Column(Integer, primary_key=True)
    whatsapp_number = Column(String(20), unique=True, index=True, nullable=False)
    
    # --- Status & Identification ---
    status = Column(String(20), default="inactive") # active, expired, blocked
    plan_name = Column(String(50), nullable=True)   # Buddy Start, Buddy Pro, etc.
    is_recurring = Column(Boolean, default=False)   # True for (3,4,5), False for (1,2)
    plugnpay_customer_id = Column(String(100), nullable=True)
    
    # --- The Credit Economy ---
    credits = Column(Integer, default=15)           # Current balance (Trial = 15)
    total_purchased = Column(Integer, default=0)    # Lifetime credits bought
    message_count = Column(Integer, default=0)      # Total questions asked
    
    # --- Trial & Subscription Logic ---
    is_trial = Column(Boolean, default=True)
    subscription_start = Column(DateTime(timezone=True), nullable=True)
    subscription_end = Column(DateTime(timezone=True), nullable=True) # Expiry date
    
    # --- Timestamps ---
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class ProcessedMessage(Base):
    __tablename__ = "processed_messages"
    # WhatsApp Message ID (wamid) as Primary Key prevents duplicates
    message_id = Column(String(255), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class BookChunk(Base):
    __tablename__ = "book_chunks"
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1536)) # Dimensions for OpenAI text-embedding-3-small
    metadata_json = Column(JSON, nullable=True) # Chapter, section, page info

class ChatLog(Base):
    __tablename__ = "chat_logs"
    id = Column(Integer, primary_key=True)
    whatsapp_number = Column(String(20), index=True, nullable=False)
    user_message = Column(Text, nullable=False)
    bot_response = Column(Text, nullable=False)
    response_type = Column(String(50)) # answered, refused, error
    chunks_used = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# High-performance Vector Index
Index(
    'idx_book_embedding', 
    BookChunk.embedding, 
    postgresql_using='hnsw', 
    postgresql_with={'m': 16, 'ef_construction': 64},
    postgresql_ops={'embedding': 'vector_cosine_ops'} 
)