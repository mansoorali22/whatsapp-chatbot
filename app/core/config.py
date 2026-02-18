"""
Configuration management for WhatsApp AI Book Chatbot
Loads all environment variables from .env and makes them accessible via `settings`.
"""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field
from pydantic_settings import SettingsConfigDict
from dotenv import load_dotenv
import os

# -------------------------------
# Determine project root & .env
# -------------------------------
project_root = Path(__file__).parent.parent.parent
env_path = project_root / ".env"

print(f"[Config] config.py location: {Path(__file__)}")
print(f"[Config] Project root: {project_root}")
print(f"[Config] .env path: {env_path}")
print(f"[Config] .env exists: {env_path.exists()}")

# Load dotenv as backup
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    print("[Config] .env loaded successfully")
else:
    print("ERROR: .env file not found!")
    if project_root.exists():
        print(f"[Config] Files in {project_root}:")
        for f in project_root.iterdir():
            print(f"  - {f.name}")

# -------------------------------
# Pydantic Settings
# -------------------------------
class Settings(BaseSettings):
    # -------------------
    # Database
    # -------------------
    DATABASE_URL: str = "postgresql://temp:temp@localhost/temp"
    MAX_CHAT_LOG_MESSAGES: int = 5
    
    # -------------------
    # WhatsApp
    # -------------------
    WHATSAPP_PHONE_ID: Optional[str] = None
    WHATSAPP_ACCESS_TOKEN: Optional[str] = Field(default=None, alias="WHATSAPP_ACCESS_TOKEN")
    WHATSAPP_BUSINESS_ACCOUNT_ID: Optional[str] = None
    WEBHOOK_VERIFY_TOKEN: Optional[str] = None
    WHATSAPP_API_VERSION: str = "v21.0"
    
    # -------------------
    # Plug & Pay
    # -------------------
    PLUG_N_PAY_TOKEN: Optional[str] = Field(default=None, alias="PLUG_N_PAY_TOKEN")
    # API token for fetching order by ID (may differ from webhook token). Generate in PlugAndPay admin.
    PLUG_N_PAY_API_TOKEN: Optional[str] = Field(default=None, alias="PLUG_N_PAY_API_TOKEN")
    # Set to "1" or "true" if PlugAndPay does not send a webhook token (they may not support it)
    PLUG_N_PAY_SKIP_VERIFY: bool = False
    # Optional: base URL to fetch order by ID when webhook has no phone (e.g. https://api.plugandpay.com)
    PLUG_N_PAY_API_URL: Optional[str] = None
    
    # -------------------
    # OpenAI
    # -------------------
    OPENAI_API_KEY: str = "temp-key"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_TEMPERATURE: float = 0.0
    EMBEDDING_DIMENSION: int = 1536
    
    # -------------------
    # App Config
    # -------------------
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    DAILY_MESSAGE_LIMIT: int = 50
    
    # -------------------
    # RAG Settings
    # -------------------
    RETRIEVAL_TOP_K: int = 8
    SIMILARITY_THRESHOLD: float = 1.0
    MAX_TOKENS_RESPONSE: int = 600
    
    # -------------------
    # Book Info
    # -------------------
    BOOK_TITLE: str = "Eat like an athlete"
    DEFAULT_LANGUAGE: str = "English"
    # Total pages in the book (for ingest: assigns estimated page numbers to chunks so answers can cite "page N")
    BOOK_TOTAL_PAGES: int = 250

    # -------------------
    # Trial & plans
    # -------------------
    TRIAL_DAYS: int = 7
    TRIAL_MAX_QUESTIONS: int = 10
    TRIAL_CREDITS: int = 10
    # Show this warning when user reaches TRIAL_WARNING_AT_QUESTION (e.g. after 6 questions, on 7th answer)
    TRIAL_WARNING_AT_QUESTION: int = 7
    UPGRADE_LINK: str = "https://iamafoodie.nl/atleet-buddy"
    TRIAL_WARNING_MESSAGE_NL: str = (
        "Je free trial eindigt bijna omdat je het maximaal aantal vragen hebt gesteld. "
        "Je Buddy helpt je graag verder. Upgrade voor onbeperkte ondersteuning. "
        "Kies het pakket dat bij je past: https://iamafoodie.nl/atleet-buddy"
    )
    TRIAL_WARNING_MESSAGE_EN: str = (
        "Your free trial is almost over because you've reached the maximum number of questions. "
        "Your Buddy is happy to help you further. Upgrade for unlimited support. "
        "Choose the plan that suits you: https://iamafoodie.nl/atleet-buddy"
    )
    UPGRADE_REQUIRED_MESSAGE_NL: str = (
        "Je trial is afgelopen of je hebt geen credits meer. "
        "Upgrade voor onbeperkte ondersteuning: https://iamafoodie.nl/atleet-buddy"
    )
    UPGRADE_REQUIRED_MESSAGE_EN: str = (
        "Your trial has ended or you have no credits left. "
        "Upgrade for unlimited support: https://iamafoodie.nl/atleet-buddy"
    )
    # When order API returns no plan/credits, grant this many credits so payment still unlocks the user
    DEFAULT_PAYMENT_CREDITS: int = 50

    # -------------------
    # Pydantic Config
    # -------------------
    model_config = SettingsConfigDict(
        env_file=env_path,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

# -------------------------------
# Instantiate settings
# -------------------------------
settings = Settings()

# -------------------------------
# Optional debug print to verify
# -------------------------------
print("\n[Config] Settings loaded successfully:")
print(f"  DATABASE_URL: {settings.DATABASE_URL[:50]}...")
print(f"  OPENAI_API_KEY: {settings.OPENAI_API_KEY[:7]}...")
print(f"  WHATSAPP_ACCESS_TOKEN: {str(settings.WHATSAPP_ACCESS_TOKEN)[:7]}...")
print(f"  WHATSAPP_PHONE_ID: {settings.WHATSAPP_PHONE_ID}")
print(f"  WHATSAPP_BUSINESS_ACCOUNT_ID: {settings.WHATSAPP_BUSINESS_ACCOUNT_ID}")
print(f"  WEBHOOK_VERIFY_TOKEN: {settings.WEBHOOK_VERIFY_TOKEN}")
print(f"  PLUG_N_PAY_TOKEN: {settings.PLUG_N_PAY_TOKEN}")
