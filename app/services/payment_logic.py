"""
Payment and subscription logic for Plug & Pay webhook events.
All operations use the Subscription table; no external API calls.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Any

from sqlalchemy.orm import Session

from app.db.models import Subscription
from app.core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subscription verification (for WhatsApp handler)
# ---------------------------------------------------------------------------

def verify_subscription(whatsapp_number: str, db: Session) -> bool:
    """
    Returns True if the user has an active subscription and credits.
    Used before processing a message in the WhatsApp handler.
    """
    sub = db.query(Subscription).filter(
        Subscription.whatsapp_number == whatsapp_number
    ).first()
    if not sub:
        return False
    if sub.status != "active":
        return False
    if sub.credits is not None and sub.credits < 1:
        return False
    if sub.subscription_end and sub.subscription_end < datetime.now(timezone.utc):
        return False
    return True


def check_credits(whatsapp_number: str, db: Session) -> int:
    """
    Returns current credit balance for the user. Returns 0 if no subscription.
    """
    sub = db.query(Subscription).filter(
        Subscription.whatsapp_number == whatsapp_number
    ).first()
    if not sub:
        return 0
    return sub.credits if sub.credits is not None else 0


def deduct_credit(whatsapp_number: str, db: Session) -> bool:
    """
    Deducts one credit after a message is answered.
    Returns True if deduction succeeded, False if no credits or no subscription.
    """
    sub = db.query(Subscription).filter(
        Subscription.whatsapp_number == whatsapp_number
    ).first()
    if not sub:
        return False
    if sub.credits is None or sub.credits < 1:
        return False
    sub.credits -= 1
    sub.message_count = (sub.message_count or 0) + 1
    db.commit()
    logger.info(f"Credits deducted for {whatsapp_number}. Remaining: {sub.credits}")
    return True


def get_subscription(whatsapp_number: str, db: Session) -> Optional[Subscription]:
    """Get subscription row by WhatsApp number (E.164)."""
    return db.query(Subscription).filter(
        Subscription.whatsapp_number == whatsapp_number
    ).first()


# ---------------------------------------------------------------------------
# Rate limit (optional, uses DAILY_MESSAGE_LIMIT from config)
# ---------------------------------------------------------------------------

def check_rate_limit(whatsapp_number: str, db: Session) -> bool:
    """
    Returns True if user is under daily message limit.
    Uses Subscription.message_count; reset logic can be extended per-day if needed.
    For MVP we only enforce credits; rate limit is optional.
    """
    limit = getattr(settings, "DAILY_MESSAGE_LIMIT", 50) or 50
    sub = get_subscription(whatsapp_number, db)
    if not sub:
        return True
    # Simple check: total message_count (could be refined to daily window)
    return (sub.message_count or 0) < limit


# ---------------------------------------------------------------------------
# Webhook event handling: create/update Subscription from Plug & Pay payload
# ---------------------------------------------------------------------------

def normalize_whatsapp_number(value: Any) -> Optional[str]:
    """
    Ensure WhatsApp number is E.164-like string (digits, optional leading +).
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Remove spaces and common separators; keep digits and leading +
    digits = "".join(c for c in s if c.isdigit() or c == "+")
    if digits.startswith("+"):
        return digits
    return "+" + digits if digits else None


def handle_subscription_created(
    whatsapp_number: str,
    db: Session,
    *,
    plan_name: Optional[str] = None,
    plugnpay_customer_id: Optional[str] = None,
    credits: Optional[int] = None,
    is_recurring: bool = False,
    subscription_end: Optional[datetime] = None,
) -> Subscription:
    """
    Create or update subscription on 'subscription_created' / 'payment_received'.
    If record exists, update it; otherwise create.
    """
    number = normalize_whatsapp_number(whatsapp_number)
    if not number:
        raise ValueError("whatsapp_number is required and must be non-empty")

    sub = get_subscription(number, db)
    now = datetime.now(timezone.utc)

    if sub:
        sub.status = "active"
        sub.plan_name = plan_name or sub.plan_name
        sub.plugnpay_customer_id = plugnpay_customer_id or sub.plugnpay_customer_id
        if credits is not None:
            sub.credits = (sub.credits or 0) + credits
            sub.total_purchased = (sub.total_purchased or 0) + credits
        sub.is_recurring = is_recurring
        sub.is_trial = False
        sub.subscription_start = sub.subscription_start or now
        sub.subscription_end = subscription_end or sub.subscription_end
        sub.updated_at = now
        db.commit()
        db.refresh(sub)
        logger.info(f"Subscription updated for {number}, plan={plan_name}, credits+= {credits}")
        return sub

    sub = Subscription(
        whatsapp_number=number,
        status="active",
        plan_name=plan_name or "Default Plan",
        plugnpay_customer_id=plugnpay_customer_id,
        credits=credits if credits is not None else 15,
        total_purchased=credits if credits is not None else 0,
        is_trial=False,
        is_recurring=is_recurring,
        subscription_start=now,
        subscription_end=subscription_end,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    logger.info(f"Subscription created for {number}, plan={plan_name}, credits={sub.credits}")
    return sub


def handle_subscription_updated(
    whatsapp_number: str,
    db: Session,
    *,
    plan_name: Optional[str] = None,
    status: Optional[str] = None,
    credits: Optional[int] = None,
    is_recurring: Optional[bool] = None,
    subscription_end: Optional[datetime] = None,
) -> Optional[Subscription]:
    """Update existing subscription (plan change, renewal, etc.)."""
    number = normalize_whatsapp_number(whatsapp_number)
    if not number:
        return None

    sub = get_subscription(number, db)
    if not sub:
        logger.warning(f"Subscription update for unknown number: {number}")
        return None

    if plan_name is not None:
        sub.plan_name = plan_name
    if status is not None:
        sub.status = status
    if credits is not None:
        sub.credits = (sub.credits or 0) + credits
        sub.total_purchased = (sub.total_purchased or 0) + credits
    if is_recurring is not None:
        sub.is_recurring = is_recurring
    if subscription_end is not None:
        sub.subscription_end = subscription_end

    sub.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sub)
    logger.info(f"Subscription updated for {number}")
    return sub


def handle_subscription_cancelled(whatsapp_number: str, db: Session) -> Optional[Subscription]:
    """Set subscription status to inactive/expired (no delete)."""
    number = normalize_whatsapp_number(whatsapp_number)
    if not number:
        return None

    sub = get_subscription(number, db)
    if not sub:
        return None

    sub.status = "expired"
    sub.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sub)
    logger.info(f"Subscription cancelled for {number}")
    return sub


def process_webhook_event(event_type: str, data: dict, db: Session) -> bool:
    """
    Dispatch webhook event to the right handler.
    Expects data to contain at least:
      - whatsapp_number (E.164)
      - optional: plan_name, plugnpay_customer_id, credits, is_recurring,
                  subscription_end (ISO string or datetime), status
    Returns True if handled successfully.
    """
    whatsapp_number = data.get("whatsapp_number") or data.get("whatsapp") or data.get("phone")
    if not whatsapp_number:
        logger.warning("Webhook event missing whatsapp_number (or phone/whatsapp key)")
        return False

    number = normalize_whatsapp_number(whatsapp_number)
    if not number:
        logger.warning("Webhook event: invalid whatsapp_number")
        return False

    event_lower = (event_type or "").strip().lower()

    # Parse subscription_end if present (ISO string)
    sub_end = data.get("subscription_end")
    if isinstance(sub_end, str):
        try:
            sub_end = datetime.fromisoformat(sub_end.replace("Z", "+00:00"))
        except Exception:
            sub_end = None

    try:
        if event_lower in ("subscription_created", "subscription.created", "payment_received", "payment.received", "new_simple_sale"):
            handle_subscription_created(
                number,
                db,
                plan_name=data.get("plan_name"),
                plugnpay_customer_id=data.get("plugnpay_customer_id") or data.get("customer_id"),
                credits=data.get("credits"),
                is_recurring=data.get("is_recurring", False),
                subscription_end=sub_end,
            )
            return True

        if event_lower in ("subscription_updated", "subscription.updated", "subscription_renewed"):
            handle_subscription_updated(
                number,
                db,
                plan_name=data.get("plan_name"),
                status=data.get("status"),
                credits=data.get("credits"),
                is_recurring=data.get("is_recurring"),
                subscription_end=sub_end,
            )
            return True

        if event_lower in ("subscription_cancelled", "subscription.cancelled", "subscription_canceled"):
            handle_subscription_cancelled(number, db)
            return True

        logger.info(f"Unhandled webhook event type: {event_type}")
        return False

    except Exception as e:
        logger.exception(f"Error processing webhook event {event_type}: {e}")
        return False
