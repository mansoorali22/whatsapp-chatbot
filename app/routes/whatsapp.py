"""
WhatsApp webhook routes for receiving and sending messages
"""
from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse
import logging
import json

from app.config import settings
from app.services.whatsapp_service import WhatsAppService
from app.services.subscription_service import SubscriptionService
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize services
whatsapp_service = WhatsAppService()
subscription_service = SubscriptionService()
rag_service = RAGService()


@router.get("/whatsapp")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """
    WhatsApp webhook verification endpoint
    Meta will call this to verify the webhook URL
    """
    logger.info(f"Webhook verification request: mode={hub_mode}")
    
    if hub_mode == "subscribe" and hub_verify_token == settings.WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verification successful")
        return PlainTextResponse(content=hub_challenge)
    
    logger.warning("Webhook verification failed - invalid token")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def receive_message(request: Request):
    """
    WhatsApp webhook endpoint for receiving messages
    """
    try:
        body = await request.json()
        logger.info(f"Received webhook: {json.dumps(body, indent=2)}")
        
        # Extract message data
        if body.get("object") != "whatsapp_business_account":
            logger.warning("Invalid webhook object type")
            return {"status": "ignored"}
        
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                
                # Check if it's a message
                messages = value.get("messages", [])
                if not messages:
                    logger.info("No messages in webhook")
                    continue
                
                for message in messages:
                    await process_message(message, value)
        
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        # Return 200 to avoid Meta retrying
        return {"status": "error", "message": str(e)}


async def process_message(message: dict, value: dict):
    """Process a single incoming message"""
    try:
        # Extract message details
        from_number = message.get("from")
        message_id = message.get("id")
        message_type = message.get("type")
        
        logger.info(f"Processing message {message_id} from {from_number}")
        
        # Only handle text messages
        if message_type != "text":
            logger.info(f"Ignoring non-text message type: {message_type}")
            return
        
        text_body = message.get("text", {}).get("body", "")
        if not text_body:
            logger.warning("Empty message body")
            return
        
        # Check subscription
        subscription = await subscription_service.get_subscription(from_number)
        
        if not subscription or subscription["status"] != "active":
            logger.warning(f"Unauthorized access attempt from {from_number}")
            response_text = (
                "⚠️ You don't have an active subscription.\n\n"
                "To access the AI Book chatbot, please subscribe at: [checkout link]"
            )
            await whatsapp_service.send_message(from_number, response_text)
            
            # Log unauthorized attempt
            await subscription_service.log_chat(
                whatsapp_number=from_number,
                user_message=text_body,
                bot_response=response_text,
                response_type="unauthorized"
            )
            return
        
        # Check rate limit
        if not await subscription_service.check_rate_limit(from_number):
            logger.warning(f"Rate limit exceeded for {from_number}")
            response_text = (
                "⚠️ Daily message limit reached.\n\n"
                f"You've reached your daily limit of {settings.DAILY_MESSAGE_LIMIT} messages. "
                "Please try again tomorrow."
            )
            await whatsapp_service.send_message(from_number, response_text)
            return
        
        # Process question with RAG
        logger.info(f"Query: {text_body}")
        rag_response = await rag_service.answer_question(text_body)
        
        # Format response
        if rag_response["response_type"] == "answered":
            response_text = rag_response["answer"]
        else:
            # Refusal case
            response_text = (
                "❌ I can't answer this based on the book.\n\n"
                "I can only provide information that's explicitly covered in "
                f"'{settings.BOOK_TITLE}'. Your question doesn't seem to be addressed in the book.\n\n"
                "💡 Try asking:\n"
                "- A more specific question about nutrition or athletic performance\n"
                "- Questions about topics covered in the book's table of contents\n"
                "- Or rephrase your question differently"
            )
        
        # Send response
        await whatsapp_service.send_message(from_number, response_text)
        
        # Log the interaction
        await subscription_service.log_chat(
            whatsapp_number=from_number,
            user_message=text_body,
            bot_response=response_text,
            response_type=rag_response["response_type"],
            chunks_used=rag_response.get("chunks_used"),
            tokens_used=rag_response.get("tokens_used"),
            retrieval_score=rag_response.get("confidence_score")
        )
        
        # Increment message count
        await subscription_service.increment_message_count(from_number)
        
        logger.info(f"Response sent successfully to {from_number}")
        
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        try:
            error_text = (
                "⚠️ Sorry, something went wrong processing your message.\n\n"
                "Please try again or contact support if the issue persists."
            )
            await whatsapp_service.send_message(message.get("from"), error_text)
        except:
            pass