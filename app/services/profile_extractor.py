"""
D/E1 — Profile extraction service.

Parses user messages for personal profile data (weight, height, age, goals,
sport, dietary preferences, training frequency) and upserts into user_profiles.

Designed to be lightweight:
  1. Quick keyword check to avoid unnecessary LLM calls
  2. Small extraction prompt → structured JSON → upsert
"""

import json
import re
from sqlalchemy.orm import Session
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from app.core.config import settings
from app.db.models import UserProfile

# ── Keywords that hint the message may contain profile info ──
_PROFILE_KEYWORDS = [
    # Weight
    "weigh", "kg", "kilo", "pound", "lbs", "gewicht",
    # Height
    "tall", "height", "cm", "meter", "lang", "lengte",
    # Age
    "years old", "jaar oud", "age", "leeftijd",
    # Goals
    "goal", "doel", "lose weight", "afvallen", "gain muscle", "spiermassa",
    "bulk", "cut", "aankomen", "muscle gain", "weight loss", "endurance",
    "uithoudingsvermogen",
    # Sport
    "sport", "run", "cycling", "fietsen", "swim", "zwemmen", "gym",
    "crossfit", "voetbal", "football", "soccer", "tennis", "hockey",
    "rowing", "roeien", "triathlon", "weightlifting", "powerlifting",
    # Diet
    "vegetarian", "vegetarisch", "vegan", "veganistisch", "lactose",
    "gluten", "halal", "pescatarian", "keto", "paleo", "allergi",
    # Training frequency
    "per week", "keer per", "times a week", "x per week", "sessions",
    "sessies", "train", "trainingen",
]

# Extraction prompt — kept small to minimise cost (runs on gpt-4o-mini)
_EXTRACTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a data extraction assistant. Extract personal profile fields from the conversation below.\n\n"
     "Return a JSON object with ONLY the fields you can confidently extract. "
     "Use null for fields you cannot determine. Do NOT guess or infer values that are not stated.\n\n"
     "Fields:\n"
     '  "weight_kg": number or null (convert lbs to kg if needed)\n'
     '  "height_cm": number or null (convert m to cm if needed)\n'
     '  "age": integer or null\n'
     '  "goals": string or null (e.g. "muscle gain", "weight loss", "endurance", "general fitness")\n'
     '  "sport": string or null (e.g. "running", "cycling", "gym", "football")\n'
     '  "dietary_preferences": string or null (e.g. "vegetarian", "no lactose", "vegan")\n'
     '  "training_frequency": string or null (e.g. "4x per week", "daily")\n\n'
     "Return ONLY valid JSON, no markdown, no explanation."),
    ("human", "User message: {user_message}\nBot response: {bot_response}"),
])

# Lazy-init LLM (reuse the app's model)
_extraction_llm = None


def _get_extraction_llm():
    global _extraction_llm
    if _extraction_llm is None:
        _extraction_llm = ChatOpenAI(
            model=settings.OPENAI_MODEL,
            temperature=0.0,
            max_tokens=300,
        )
    return _extraction_llm


def _message_has_profile_hints(text: str) -> bool:
    """Quick keyword check — avoids an LLM call for 95%+ of messages."""
    lower = text.lower()
    return any(kw in lower for kw in _PROFILE_KEYWORDS)


def extract_profile_fields(user_message: str, bot_response: str) -> dict:
    """
    Extract structured profile data from a conversation turn.
    Returns dict of non-null fields (e.g. {"weight_kg": 75, "sport": "running"})
    or empty dict if nothing found.
    """
    if not _message_has_profile_hints(user_message):
        return {}

    try:
        llm = _get_extraction_llm()
        chain = _EXTRACTION_PROMPT | llm
        result = chain.invoke({
            "user_message": user_message,
            "bot_response": bot_response,
        })
        raw = result.content.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        # Filter out null values
        return {k: v for k, v in data.items() if v is not None}
    except Exception as e:
        print(f"⚠️ Profile extraction failed (non-fatal): {e}")
        return {}


def upsert_profile(db: Session, whatsapp_number: str, fields: dict) -> None:
    """
    Create or update the user profile with extracted fields.
    Only updates fields that are present in `fields` dict — never overwrites
    existing data with null.
    """
    if not fields:
        return

    # Whitelist of allowed columns
    allowed = {
        "weight_kg", "height_cm", "age", "goals", "sport",
        "dietary_preferences", "training_frequency",
    }
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return

    profile = db.query(UserProfile).filter_by(whatsapp_number=whatsapp_number).first()

    if profile:
        for key, value in clean.items():
            setattr(profile, key, value)
    else:
        profile = UserProfile(whatsapp_number=whatsapp_number, **clean)
        db.add(profile)

    try:
        db.commit()
        print(f"✅ Profile updated for {whatsapp_number}: {clean}")
    except Exception as e:
        db.rollback()
        print(f"⚠️ Profile upsert failed (non-fatal): {e}")
