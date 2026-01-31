import logging
from fastapi import APIRouter, Request, Response, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session
import httpx

from app.db.connection import get_db
from app.core.config import settings
from app.services.rag import get_response  # Your RAG logic
from app.models import Subscription, ChatLog, ProcessedMessage

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
async def handle_rag_and_reply(sender: str, text: str, db: Session):
    """
    Processes the RAG logic and sends a reply to the sender asynchronously.
    """
    ai_answer = get_response(text, sender, db)
    await send_whatsapp_message(sender, ai_answer)

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
    """
    if mode == "subscribe" and token == settings.WEBHOOK_VERIFY_TOKEN:
        logger.info("✅ Webhook Verified Successfully!")
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Verification failed", status_code=403)

# -------------------------------
# 2️⃣ RECEIVE INCOMING MESSAGES (POST)
# -------------------------------
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

        entry = body["entry"][0]
        changes = entry.get("changes", [])

        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])

            for message in messages:
                message_id = message.get("id")
                sender = message.get("from")
                text = message.get("text", {}).get("body")

                if not sender or not text or not message_id:
                    continue

                # --- Skip already processed messages ---
                if db.query(ProcessedMessage).filter_by(message_id=message_id).first():
                    continue

                # --- Store processed message ID ---
                db.add(ProcessedMessage(message_id=message_id))

                # --- Treat every number as subscribed ---
                subscription = db.query(Subscription).filter_by(whatsapp_number=sender).first()
                if not subscription:
                    subscription = Subscription(
                        whatsapp_number=sender,
                        status="active",
                        plan_name="Default Plan",
                        is_trial=True
                    )
                    db.add(subscription)
                    db.commit()  # commit to get ID for message_count increment

                subscription.message_count += 1

                # --- Store chat log ---
                chat_log = ChatLog(
                    whatsapp_number=sender,
                    user_message=text,
                    bot_response="Message received!",
                    response_type="static_reply"
                )
                db.add(chat_log)
                db.commit()

                # --- Keep only the last N messages per user ---
                max_messages = settings.MAX_CHAT_LOG_MESSAGES
                to_delete = (
                    db.query(ChatLog)
                    .filter_by(whatsapp_number=sender)
                    .order_by(ChatLog.created_at.desc())
                    .offset(max_messages)
                    .all()
                )
                for old_msg in to_delete:
                    db.delete(old_msg)
                db.commit()

                # --- Send static reply ---
                background_tasks.add_task(send_whatsapp_message, sender, "Good Message Received!")

                logger.info(f"📩 NEW MESSAGE FROM {sender}: {text}")

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
