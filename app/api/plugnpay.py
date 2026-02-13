"""
Plug & Pay webhook endpoint.
Receives payment/subscription events and updates the Subscription table via payment_logic.
"""
import logging
import os
import re
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.connection import get_db
from app.core.config import settings
from app.services.payment_logic import process_webhook_event

router = APIRouter()
logger = logging.getLogger(__name__)


# Header names and body keys that may carry the webhook secret (Plug&Pay / PlugAndPay vary)
_WEBHOOK_TOKEN_HEADERS = (
    "X-Webhook-Token",
    "X-Webhook-Secret",
    "X-PlugAndPay-Token",
    "X-Plug-And-Pay-Token",
    "Authorization",
)
_WEBHOOK_TOKEN_BODY_KEYS = ("webhook_token", "verify_token", "secret", "token", "webhook_secret")

# Keys that might hold a phone number in webhook payloads
_PHONE_KEYS = ("whatsapp_number", "whatsapp", "phone", "phone_number", "mobile", "telephone")


# PlugAndPay API (from https://github.com/plug-and-pay/sdk-php: BASE_API_URL_PRODUCTION, OrderService uses GET /v2/orders/{id})
PLUGANDPAY_API_BASE_DEFAULT = "https://api.plugandpay.com"
PLUGANDPAY_ORDER_PATH = "/v2/orders/{id}"


async def _fetch_order_phone(order_id: int) -> Optional[str]:
    """
    Fetch order by ID from PlugAndPay API and return customer phone.
    Uses https://api.plugandpay.com and GET /v2/orders/{id} (per plug-and-pay/sdk-php).
    Response body has order in body["data"]; we search for phone in data and nested objects.
    """
    api_url = (
        getattr(settings, "PLUG_N_PAY_API_URL", None)
        or os.environ.get("PLUG_N_PAY_API_URL", "").strip().rstrip("/")
        or PLUGANDPAY_API_BASE_DEFAULT
    )
    # Use API token if set (from admin); else fall back to webhook token (401 = need API token from PlugAndPay admin)
    token = (
        getattr(settings, "PLUG_N_PAY_API_TOKEN", None)
        or os.environ.get("PLUG_N_PAY_API_TOKEN", "").strip()
        or getattr(settings, "PLUG_N_PAY_TOKEN", None)
        or os.environ.get("PLUG_N_PAY_TOKEN")
    )
    if not token:
        logger.warning("PlugAndPay API fetch skipped: PLUG_N_PAY_API_TOKEN or PLUG_N_PAY_TOKEN not set on Render")
        return None
    path = PLUGANDPAY_ORDER_PATH.format(id=order_id)
    url = api_url.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            if r.status_code != 200:
                logger.warning("PlugAndPay API order %s returned %s: %s", order_id, r.status_code, r.text[:200] if r.text else "")
                return None
            data = r.json()
            if not isinstance(data, dict):
                return None
            # API returns { "data": { ... order ... } } per SDK BodyToOrder::build($response->body()['data'])
            payload = data.get("data") if isinstance(data.get("data"), dict) else data
            phone = _find_phone_in_dict(payload)
            if phone:
                logger.info("Fetched order %s from PlugAndPay API; found phone", order_id)
                return phone
            for key in ("order", "receiver", "customer", "billing", "data"):
                if isinstance(data.get(key), dict):
                    phone = _find_phone_in_dict(data[key])
                    if phone:
                        return phone
            logger.warning("PlugAndPay API order %s: response 200 but no phone field in JSON (top-level keys: %s)", order_id, list(data.keys()) if isinstance(data, dict) else "n/a")
    except Exception as e:
        logger.warning("PlugAndPay API fetch order %s failed: %s", order_id, e)
    return None


def _find_phone_in_dict(d: Any, depth: int = 0, max_depth: int = 4) -> Optional[str]:
    """Recursively find first string that looks like a phone in dict (for varying payload shapes)."""
    if depth > max_depth or not isinstance(d, dict):
        return None
    for key in _PHONE_KEYS:
        val = d.get(key)
        if val is None:
            continue
        if isinstance(val, str) and val.strip():
            digits = "".join(c for c in val if c.isdigit())
            if len(digits) >= 8:
                return val.strip()
        if isinstance(val, dict):
            found = _find_phone_in_dict(val, depth + 1, max_depth)
            if found:
                return found
    for v in d.values():
        if isinstance(v, dict):
            found = _find_phone_in_dict(v, depth + 1, max_depth)
            if found:
                return found
    return None


def _verify_webhook_token(request: Request, body: dict) -> bool:
    """
    Verify webhook authenticity using PLUG_N_PAY_TOKEN.
    Checks common header and body field names used by Plug&Pay / PlugAndPay.
    """
    token = getattr(settings, "PLUG_N_PAY_TOKEN", None) or getattr(
        settings, "PLUGNPAY_WEBHOOK_SECRET", None
    )
    if not token:
        logger.warning("No PLUG_N_PAY_TOKEN or PLUGNPAY_WEBHOOK_SECRET set; skipping verification")
        return True

    # Headers
    for name in _WEBHOOK_TOKEN_HEADERS:
        value = request.headers.get(name)
        if value:
            compare = value.replace("Bearer ", "").strip()
            if compare == token:
                return True

    # Body
    for key in _WEBHOOK_TOKEN_BODY_KEYS:
        if body.get(key) == token:
            return True

    headers_present = [h for h in _WEBHOOK_TOKEN_HEADERS if request.headers.get(h)]
    body_present = [k for k in _WEBHOOK_TOKEN_BODY_KEYS if body.get(k) is not None]

    # PlugAndPay does not send a token; accept when provider sends none (no env var needed)
    if not headers_present and not body_present:
        logger.info("Plug&Pay webhook accepted (no token sent by provider)")
        return True

    logger.warning(
        "Webhook verification failed: invalid or missing token. "
        "Headers present: %s; body keys present: %s. "
        "Set PLUG_N_PAY_TOKEN on Render, or PLUG_N_PAY_SKIP_VERIFY=true if provider does not send a token.",
        headers_present or "none",
        body_present or "none",
    )
    return False


def _extract_event_and_data(body: dict) -> tuple[str, dict]:
    """
    Normalize Plug & Pay (and similar) webhook payload into event_type + data dict
    with whatsapp_number and optional credits, plan_name, etc.
    Handles both string type and PlugAndPay dict type: { "trigger_type": "order_invoice_created", ... }.
    """
    raw_type = body.get("type") or body.get("event") or body.get("event_type")
    if isinstance(raw_type, dict):
        event_type = raw_type.get("trigger_type") or raw_type.get("event") or "order_invoice_created"
    else:
        event_type = raw_type or "payment_received"
    if not isinstance(event_type, str):
        event_type = "payment_received"

    # Payload may have data at body.data or everything inside body.event (PlugAndPay: event, rule_id, sent_at, tenant_id)
    event_obj = body.get("event") if isinstance(body.get("event"), dict) else None
    data = body.get("data") or (event_obj or body)
    if not isinstance(data, dict):
        data = {}

    # PlugAndPay may send order/customer at top level, under data, or inside event
    order = (
        data.get("order")
        or body.get("order")
        or (event_obj.get("order") if event_obj else None)
        or {}
    )
    customer = (
        data.get("customer")
        or body.get("customer")
        or body.get("billing_details")
        or (event_obj.get("customer") if event_obj else None)
        or {}
    )
    if not isinstance(order, dict):
        order = {}
    if not isinstance(customer, dict):
        customer = {}
    custom_fields = order.get("custom_fields") or data.get("custom_fields") or body.get("custom_fields") or {}

    # WhatsApp number: custom_fields, customer, order, data, body (multiple key names)
    whatsapp_number = (
        custom_fields.get("whatsapp_number")
        or custom_fields.get("whatsapp")
        or custom_fields.get("phone")
        or customer.get("phone")
        or customer.get("phone_number")
        or customer.get("mobile")
        or customer.get("telephone")
        or order.get("customer_phone")
        or order.get("phone")
        or order.get("whatsapp_number")
        or data.get("whatsapp_number")
        or data.get("whatsapp")
        or data.get("phone")
        or body.get("whatsapp_number")
        or body.get("whatsapp")
        or body.get("phone")
    )
    # Last resort: deep search (PlugAndPay may nest order/customer differently)
    if not whatsapp_number:
        whatsapp_number = _find_phone_in_dict(body)

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


@router.api_route("/webhook", methods=["GET", "HEAD"])
async def plugnpay_webhook_verify(
    verify_token: Optional[str] = Query(None, alias="verify_token"),
):
    """
    Webhook confirmation: Plug & Pay or client can GET/HEAD this URL with verify_token
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


def _mask_number(num: str) -> str:
    """Last 4 digits only for logs (e.g. ***231166)."""
    if not num or len(num) < 4:
        return "***"
    return "***" + num[-6:] if len(num) > 6 else "***" + num[-4:]


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
        raise HTTPException(status_code=403, detail="Forbidden")

    event_type, data = _extract_event_and_data(body)

    # When webhook has no phone but has order reference (event.triggerable_id), try to fetch order from API
    if not data.get("whatsapp_number"):
        event_obj = body.get("event") if isinstance(body.get("event"), dict) else None
        trigger_type = (event_obj or {}).get("triggerable_type") or body.get("triggerable_type")
        trigger_type_str = (event_obj or {}).get("trigger_type") or body.get("trigger_type")  # e.g. "order_invoice_created"
        trigger_id = (event_obj or {}).get("triggerable_id") or body.get("triggerable_id")
        is_order = (
            trigger_id is not None
            and (
                str(trigger_type or "").lower() == "order"
                or (trigger_type_str and "order" in str(trigger_type_str).lower())
                or (trigger_type_str and "invoice" in str(trigger_type_str).lower())
            )
        )
        if is_order:
            try:
                oid = int(trigger_id)
                logger.info("Fetching order %s from PlugAndPay API (triggerable_type=%s)", oid, trigger_type)
                fetched = await _fetch_order_phone(oid)
                if fetched:
                    data["whatsapp_number"] = fetched
                else:
                    logger.warning("PlugAndPay API returned no phone for order %s", oid)
            except (TypeError, ValueError) as e:
                logger.warning("Invalid triggerable_id %s: %s", trigger_id, e)
        elif not data.get("whatsapp_number"):
            logger.info(
                "Webhook has no phone. event type=%s, keys=%s",
                type(body.get("event")).__name__ if body.get("event") is not None else "missing",
                list(event_obj.keys()) if isinstance(event_obj, dict) else (list(body.keys()) if isinstance(body, dict) else "n/a"),
            )

    raw_number = (data.get("whatsapp_number") or data.get("whatsapp") or data.get("phone") or "")[:20]
    logger.info(
        "Plug&Pay webhook received: type=%s whatsapp=%s credits=%s",
        event_type,
        _mask_number(raw_number),
        data.get("credits"),
    )

    if not data.get("whatsapp_number"):
        logger.warning(
            "Webhook payload missing whatsapp_number; cannot link to subscription. "
            "Top-level keys: %s. Add phone/custom field in PlugAndPay checkout or set PLUG_N_PAY_API_URL to fetch order by ID.",
            list(body.keys()) if isinstance(body, dict) else "n/a",
        )
        return {"status": "ignored", "reason": "missing_whatsapp_number"}

    try:
        ok = process_webhook_event(event_type, data, db)
        logger.info(
            "Plug&Pay webhook handled: type=%s whatsapp=%s status=%s",
            event_type,
            _mask_number(data.get("whatsapp_number", "")),
            "ok" if ok else "ignored",
        )
        return {"status": "ok" if ok else "ignored", "event_type": event_type}
    except Exception as e:
        logger.exception(f"Webhook processing error: {e}")
        # Still return 200 so Plug & Pay does not retry indefinitely
        return {"status": "error", "event_type": event_type, "message": str(e)}
