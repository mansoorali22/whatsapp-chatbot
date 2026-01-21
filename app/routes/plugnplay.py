"""
Plug & Pay webhook routes for subscription management
"""
from fastapi import APIRouter, Request, HTTPException, Header
from typing import Optional
import logging
import hmac
import hashlib

from app.config import settings
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)
router = APIRouter()

subscription_service = SubscriptionService()


def verify_webhook_signature(payload: bytes, signature: Optional[str]) -> bool:
    """Verify Plug & Pay webhook signature"""
    if not settings.PLUGNPAY_WEBHOOK_SECRET:
        logger.warning("PLUGNPAY_WEBHOOK_SECRET not configured, skipping signature verification")
        return True
    
    if not signature:
        return False
    
    expected_signature = hmac.new(
        settings.PLUGNPAY_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected_signature)


@router.post("/webhook")
async def handle_webhook(
    request: Request,
    x_plugnpay_signature: Optional[str] = Header(None)
):
    """
    Plug & Pay webhook endpoint for subscription events
    
    Expected events:
    - subscription.created
    - subscription.updated
    - subscription.cancelled
    - subscription.expired
    - payment.succeeded
    - payment.failed
    """
    try:
        # Get raw body for signature verification
        body_bytes = await request.body()
        
        # Verify signature
        if not verify_webhook_signature(body_bytes, x_plugnpay_signature):
            logger.warning("Invalid webhook signature")
            raise HTTPException(status_code=403, detail="Invalid signature")
        
        # Parse JSON
        data = await request.json()
        event_type = data.get("event", data.get("type", "unknown"))
        
        logger.info(f"Received Plug & Pay webhook: {event_type}")
        logger.debug(f"Webhook data: {data}")
        
        # Handle different event types
        if event_type in ["subscription.created", "payment.succeeded"]:
            await handle_subscription_activated(data)
        
        elif event_type == "subscription.updated":
            await handle_subscription_updated(data)
        
        elif event_type in ["subscription.cancelled", "subscription.expired"]:
            await handle_subscription_deactivated(data)
        
        elif event_type == "payment.failed":
            await handle_payment_failed(data)
        
        else:
            logger.info(f"Unhandled event type: {event_type}")
        
        return {"status": "ok", "event": event_type}
    
    except Exception as e:
        logger.error(f"Error processing Plug & Pay webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def handle_subscription_activated(data: dict):
    """Handle subscription activation"""
    try:
        customer_id = data.get("customer", {}).get("id") or data.get("customer_id")
        whatsapp_number = extract_whatsapp_number(data)
        
        if not whatsapp_number:
            logger.error("WhatsApp number not found in webhook data")
            return
        
        subscription_data = data.get("subscription", {})
        
        await subscription_service.create_or_update_subscription(
            whatsapp_number=whatsapp_number,
            status="active",
            plugnpay_customer_id=customer_id,
            subscription_start=subscription_data.get("start_date"),
            subscription_end=subscription_data.get("end_date")
        )
        
        logger.info(f"Subscription activated for {whatsapp_number}")
    
    except Exception as e:
        logger.error(f"Error activating subscription: {e}", exc_info=True)


async def handle_subscription_updated(data: dict):
    """Handle subscription update"""
    try:
        whatsapp_number = extract_whatsapp_number(data)
        if not whatsapp_number:
            return
        
        subscription_data = data.get("subscription", {})
        status = subscription_data.get("status", "active")
        
        await subscription_service.update_subscription_status(
            whatsapp_number=whatsapp_number,
            status=status,
            subscription_end=subscription_data.get("end_date")
        )
        
        logger.info(f"Subscription updated for {whatsapp_number}: status={status}")
    
    except Exception as e:
        logger.error(f"Error updating subscription: {e}", exc_info=True)


async def handle_subscription_deactivated(data: dict):
    """Handle subscription cancellation or expiration"""
    try:
        whatsapp_number = extract_whatsapp_number(data)
        if not whatsapp_number:
            return
        
        await subscription_service.update_subscription_status(
            whatsapp_number=whatsapp_number,
            status="expired"
        )
        
        logger.info(f"Subscription deactivated for {whatsapp_number}")
    
    except Exception as e:
        logger.error(f"Error deactivating subscription: {e}", exc_info=True)


async def handle_payment_failed(data: dict):
    """Handle failed payment"""
    try:
        whatsapp_number = extract_whatsapp_number(data)
        if not whatsapp_number:
            return
        
        # Optionally block or flag the subscription
        logger.warning(f"Payment failed for {whatsapp_number}")
    
    except Exception as e:
        logger.error(f"Error handling payment failure: {e}", exc_info=True)


def extract_whatsapp_number(data: dict) -> Optional[str]:
    """
    Extract WhatsApp number from webhook data
    
    This depends on how Plug & Pay is configured.
    Common locations:
    - data["customer"]["phone"]
    - data["customer"]["whatsapp"]
    - data["metadata"]["whatsapp_number"]
    """
    # Try different possible locations
    customer = data.get("customer", {})
    metadata = data.get("metadata", {})
    
    whatsapp_number = (
        customer.get("whatsapp") or
        customer.get("phone") or
        metadata.get("whatsapp_number") or
        metadata.get("phone")
    )
    
    if whatsapp_number:
        # Ensure E.164 format
        if not whatsapp_number.startswith("+"):
            whatsapp_number = f"+{whatsapp_number}"
    
    return whatsapp_number


@router.get("/test")
async def test_endpoint():
    """Test endpoint to verify Plug & Pay webhook is reachable"""
    return {
        "service": "Plug & Pay webhook",
        "status": "ready",
        "signature_verification": bool(settings.PLUGNPAY_WEBHOOK_SECRET)
    }