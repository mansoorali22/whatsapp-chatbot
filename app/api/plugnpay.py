"""
Plug & Pay webhook endpoint.
Receives payment/subscription events and updates the Subscription table via payment_logic.
"""
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.connection import get_db
from app.core.config import settings
from app.services.payment_logic import process_webhook_event

router = APIRouter()
logger = logging.getLogger(__name__)


def _verify_webhook_token(request: Request, body: dict) -> bool:
    """
    Verify webhook authenticity using PLUG_N_PAY_TOKEN.
    Checks common patterns: header (X-Webhook-Token, Authorization) or body field.
    """
    token = getattr(settings, "PLUG_N_PAY_TOKEN", None) or getattr(
        settings, "PLUGNPAY_WEBHOOK_SECRET", None
    )
    if not token:
        logger.warning("No PLUG_N_PAY_TOKEN or PLUGNPAY_WEBHOOK_SECRET set; skipping verification")
        return True

    # Header: X-Webhook-Token or Authorization Bearer
    auth_header = request.headers.get("X-Webhook-Token") or request.headers.get("Authorization")
    if auth_header and auth_header.replace("Bearer ", "").strip() == token:
        return True
    if body.get("webhook_token") == token or body.get("verify_token") == token:
        return True
    return False


def _extract_event_and_data(body: dict) -> tuple[str, dict]:
    """
    Normalize Plug & Pay (and similar) webhook payload into event_type + data dict
    with whatsapp_number and optional credits, plan_name, etc.
    """
    # Plug & Pay style: { "type": "new_simple_sale", "data": { "order": {...}, "customer": {...} } }
    event_type = body.get("type") or body.get("event") or body.get("event_type") or "payment_received"
    data = body.get("data") or body

    # Flatten: if data is the full payload, use it; else build from order/customer
    if not isinstance(data, dict):
        data = {}

    order = data.get("order") or {}
    customer = data.get("customer") or data.get("billing_details") or {}
    custom_fields = order.get("custom_fields") or data.get("custom_fields") or {}

    # WhatsApp number: custom_fields (E.164), then customer.phone, then top-level
    whatsapp_number = (
        custom_fields.get("whatsapp_number")
        or custom_fields.get("whatsapp")
        or custom_fields.get("phone")
        or customer.get("phone")
        or data.get("whatsapp_number")
        or data.get("whatsapp")
        or data.get("phone")
        or body.get("whatsapp_number")
        or body.get("whatsapp")
        or body.get("phone")
    )

    # Credits: from product metadata, custom_fields, or fixed amount
    credits = (
        custom_fields.get("credits")
        or data.get("credits")
        or body.get("credits")
    )
    if credits is not None:
        try:
            credits = int(credits)
        except (TypeError, ValueError):
            credits = None

    # Plan name
    plan_name = (
        custom_fields.get("plan_name")
        or data.get("plan_name")
        or body.get("plan_name")
    )
    if not plan_name and order.get("products"):
        first = order["products"][0] if order["products"] else {}
        plan_name = first.get("title") or first.get("name") or first.get("slug") or ""

    # Credits: also derive from product name/slug (e.g. atleet-buddy-credits-50 → 50)
    if credits is None and plan_name:
        m = re.search(r"credits[-_]?(\d+)|(\d+)\s*credits", plan_name, re.I)
        if m:
            credits = int(m.group(1) or m.group(2))

    # Customer ID from payment provider
    plugnpay_customer_id = (
        str(customer.get("id")) if customer.get("id") is not None else None
    ) or data.get("customer_id") or body.get("customer_id")

    # Build normalized data for payment_logic
    normalized = {
        "whatsapp_number": whatsapp_number,
        "plan_name": plan_name,
        "plugnpay_customer_id": plugnpay_customer_id,
        "credits": credits,
        "is_recurring": data.get("is_recurring", body.get("is_recurring", False)),
        "subscription_end": data.get("subscription_end") or body.get("subscription_end"),
        "status": data.get("status") or body.get("status"),
    }
    return event_type, normalized


@router.get("")
async def plugpay_root():
    """Confirm Plug&Pay routes are mounted (e.g. GET /plugpay and GET /plugpay/webhook)."""
    return {"service": "Plug&Pay webhook", "verify": "/plugpay/webhook?verify_token=YOUR_TOKEN"}


@router.get("/webhook")
async def plugnpay_webhook_verify(
    verify_token: Optional[str] = Query(None, alias="verify_token"),
):
    """
    Webhook confirmation: Plug & Pay or client can GET this URL with verify_token
    to confirm the endpoint is valid. Returns 200 if token matches PLUG_N_PAY_TOKEN.
    """
    token = getattr(settings, "PLUG_N_PAY_TOKEN", None) or getattr(
        settings, "PLUGNPAY_WEBHOOK_SECRET", None
    )
    if not token:
        return {"status": "ok", "message": "Webhook endpoint active (no token set)"}
    if verify_token and verify_token.strip() == token:
        logger.info("Plug & Pay webhook verification successful")
        return {"status": "verified", "message": "Webhook confirmed"}
    return {"status": "ok", "message": "Webhook endpoint active"}


@router.post("/webhook")
async def plugnpay_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Receives Plug & Pay webhook events (payment received, subscription created/updated/cancelled).
    Updates the Subscription table; responds 200 quickly so Plug & Pay does not retry.
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.warning(f"Invalid webhook body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not _verify_webhook_token(request, body):
        logger.warning("Webhook verification failed: invalid or missing token")
        raise HTTPException(status_code=403, detail="Forbidden")

    event_type, data = _extract_event_and_data(body)

    if not data.get("whatsapp_number"):
        logger.warning("Webhook payload missing whatsapp_number; cannot link to subscription")
        return {"status": "ignored", "reason": "missing_whatsapp_number"}

    try:
        ok = process_webhook_event(event_type, data, db)
        return {"status": "ok" if ok else "ignored", "event_type": event_type}
    except Exception as e:
        logger.exception(f"Webhook processing error: {e}")
        # Still return 200 so Plug & Pay does not retry indefinitely
        return {"status": "error", "event_type": event_type, "message": str(e)}
