"""
Configuration management for WhatsApp AI Book Chatbot
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str
    
    # WhatsApp
    WHATSAPP_PHONE_NUMBER_ID: str
    WHATSAPP_ACCESS_TOKEN: str
    WEBHOOK_VERIFY_TOKEN: str
    WHATSAPP_API_VERSION: str = "v21.0"
    
    # Plug & Pay
    PLUGNPAY_WEBHOOK_SECRET: Optional[str] = None
    
    # OpenAI
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSION: int = 1536
    
    # App Config
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    DAILY_MESSAGE_LIMIT: int = 50
    
    # RAG Settings
    RETRIEVAL_TOP_K: int = 5
    SIMILARITY_THRESHOLD: float = 0.7
    MAX_TOKENS_RESPONSE: int = 500
    
    # Book Info
    BOOK_TITLE: str = "Eat like an athlete"
    DEFAULT_LANGUAGE: str = "English"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()