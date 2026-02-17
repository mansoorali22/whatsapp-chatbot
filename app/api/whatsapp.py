import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, Response, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
import httpx

from app.db.connection import get_db, SessionLocal
from app.core.config import settings
from app.services.rag import get_response, _use_dutch_page_word
from app.services.payment_logic import verify_subscription, deduct_credit, get_subscription, normalize_whatsapp_number
from app.db.models import Subscription, ChatLog, ProcessedMessage

router = APIRouter()
logger = logging.getLogger(__name__)


# -------------------------------
# HELPER: SEND WHATSAPP MESSAGE
# -------------------------------
async def send_whatsapp_message(to: str, message_text: str):
    """
    Sends a WhatsApp message via Meta Graph API
    """
    url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message_text}
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"✅ Message sent to {to}")
        except Exception as e:
            logger.error(f"❌ Failed to send WhatsApp message to {to}: {e}")

# -------------------------------
# BACKGROUND TASK: RAG RESPONSE
# -------------------------------
def _is_dutch_message(text: str) -> bool:
    """True if the user message is in Dutch (for picking NL/EN upgrade and trial messages)."""
    return _use_dutch_page_word(text or "")


async def handle_rag_and_reply(sender: str, text: str, is_first_message: bool = False):
    """
    Verifies subscription/credits, runs RAG, sends reply. Deducts one credit after answering.
    Trial: at 7th answer we send the answer first, then a separate message with the trial warning (NL or EN).
    Upgrade required message is in Dutch or English based on user's message.
    """
    db = SessionLocal()
    try:
        # Use normalized number for DB so we match the same row as the payment webhook
        sender_for_db = normalize_whatsapp_number(sender) or sender
        if not verify_subscription(sender_for_db, db):
            msg = settings.UPGRADE_REQUIRED_MESSAGE_NL if _is_dutch_message(text) else settings.UPGRADE_REQUIRED_MESSAGE_EN
            await send_whatsapp_message(sender, msg)
            return
        sub = get_subscription(sender_for_db, db)
        warning_at = getattr(settings, "TRIAL_WARNING_AT_QUESTION", 7)
        show_trial_warning = sub and sub.is_trial and (sub.message_count or 0) == (warning_at - 1)
        ai_answer = get_response(text, sender_for_db, db, is_first_message=is_first_message)
        await send_whatsapp_message(sender, ai_answer)
        if show_trial_warning:
            trial_msg = settings.TRIAL_WARNING_MESSAGE_NL if _is_dutch_message(text) else settings.TRIAL_WARNING_MESSAGE_EN
            await send_whatsapp_message(sender, trial_msg)
        deduct_credit(sender_for_db, db)
    finally:
        db.close()

# -------------------------------
# 1️⃣ VERIFICATION (GET)
# -------------------------------
@router.get("/get-messages")
async def verify_whatsapp(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge")
):
    """
    Endpoint used by WhatsApp Cloud API to verify webhook.
    Meta sends GET with hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=...
    Ensure WEBHOOK_VERIFY_TOKEN in Render env matches the token set in Meta Developer Console.
    """
    expected = getattr(settings, "WEBHOOK_VERIFY_TOKEN", None) or ""
    token_ok = (token or "") == expected
    if mode == "subscribe" and token_ok:
        logger.info("✅ Webhook Verified Successfully!")
        return Response(content=challenge or "", media_type="text/plain")
    logger.warning("Webhook verification failed: mode=%s token_match=%s (set WEBHOOK_VERIFY_TOKEN on Render)", mode, token_ok)
    return Response(content="Verification failed", status_code=403)

# -------------------------------
# 2️⃣ RECEIVE INCOMING MESSAGES (POST)
# -------------------------------
def _process_webhook_messages(body: dict, db: Session, background_tasks: BackgroundTasks) -> None:
    """Process incoming webhook payload: dedupe, update subscription, queue RAG reply. Uses given db session."""
    if body.get("object") != "whatsapp_business_account":
        return
    entry = body["entry"][0]
    for change in entry.get("changes", []):
        value = change.get("value", {})
        for message in value.get("messages", []):
            message_id = message.get("id")
            sender = message.get("from")
            text = message.get("text", {}).get("body")
            if not sender or not text or not message_id:
                continue
            if db.query(ProcessedMessage).filter_by(message_id=message_id).first():
                continue
            db.add(ProcessedMessage(message_id=message_id))
            sender_for_db = normalize_whatsapp_number(sender) or sender
            subscription = db.query(Subscription).filter_by(whatsapp_number=sender_for_db).first()
            if not subscription:
                now = datetime.now(timezone.utc)
                trial_days = getattr(settings, "TRIAL_DAYS", 7)
                trial_credits = getattr(settings, "TRIAL_CREDITS", 15)
                subscription = Subscription(
                    whatsapp_number=sender_for_db,
                    status="active",
                    plan_name="Trial",
                    is_trial=True,
                    credits=trial_credits,
                    subscription_start=now,
                    subscription_end=now + timedelta(days=trial_days),
                )
                db.add(subscription)
                db.commit()
            is_first_message = db.query(ChatLog).filter(ChatLog.whatsapp_number == sender_for_db).count() == 0
            background_tasks.add_task(handle_rag_and_reply, sender, text, is_first_message)
            logger.info(f"📩 NEW MESSAGE FROM {sender}: {text}")


@router.post("/get-messages")
async def receive_message(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Receives incoming WhatsApp messages, stores them,
    keeps only the last N messages per user,
    and sends a static reply.
    Treats every number as subscribed for now.
    """
    try:
        body = await request.json()
        if body.get("object") != "whatsapp_business_account":
            return {"status": "ignored"}

        try:
            _process_webhook_messages(body, db, background_tasks)
        except OperationalError as e:
            err_str = str(e).lower()
            if "ssl" in err_str or "closed" in err_str or "connection" in err_str:
                logger.warning("⚠️ DB connection stale on webhook, retrying with fresh session...")
                db_fresh = SessionLocal()
                try:
                    _process_webhook_messages(body, db_fresh, background_tasks)
                finally:
                    db_fresh.close()
            else:
                raise

        return {"status": "success"}

    except Exception as e:
        logger.error(f"⚠️ Error parsing webhook payload: {e}")
        return {"status": "ignored"}
    
# -------------------------------
# 3️⃣ MANUAL SEND MESSAGE ENDPOINT
# -------------------------------
@router.post("/send")
async def send_message_to_user(
    to: str,
    message: str
):
    """
    Send a WhatsApp message to any number manually.
    Example JSON body:
    {
        "to": "923205038894",
        "message": "Hello! This is a test."
    }
    """
    await send_whatsapp_message(to, message)
    return {"status": "sent", "to": to, "message": message}
