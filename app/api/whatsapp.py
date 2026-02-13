import logging
from fastapi import APIRouter, Request, Response, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
import httpx

from app.db.connection import get_db, SessionLocal
from app.core.config import settings
from app.services.rag import get_response  # Your RAG logic
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
async def handle_rag_and_reply(sender: str, text: str, is_first_message: bool = False):
    """
    Processes the RAG logic and sends a reply to the sender asynchronously.
    Uses its own DB session to avoid stale connections (e.g. after app sleep).
    When is_first_message is True, the reply includes the welcome intro for new users.
    """
    db = SessionLocal()
    try:
        ai_answer = get_response(text, sender, db, is_first_message=is_first_message)
        await send_whatsapp_message(sender, ai_answer)
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
            subscription = db.query(Subscription).filter_by(whatsapp_number=sender).first()
            if not subscription:
                subscription = Subscription(
                    whatsapp_number=sender,
                    status="active",
                    plan_name="Default Plan",
                    is_trial=True
                )
                db.add(subscription)
                db.commit()
            subscription.message_count += 1
            db.commit()
            is_first_message = db.query(ChatLog).filter(ChatLog.whatsapp_number == sender).count() == 0
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
