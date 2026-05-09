"""
B6 — Scheduled alert checks.

Runs periodically (e.g. every hour via APScheduler) and creates Alert rows
when thresholds are breached. Designed to be idempotent — won't create
duplicate alerts for the same condition within the same day.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.connection import SessionLocal
from app.db.models import Alert, ChatLog, Subscription


# ── Thresholds (tuneable via env later) ──
COST_DAILY_THRESHOLD_USD = 5.00       # alert if daily cost exceeds this
MESSAGES_DAILY_THRESHOLD = 500        # alert if daily messages exceed this
REFUSAL_RATE_THRESHOLD = 0.25         # alert if >25% of messages are refused
EXPIRING_SUBS_DAYS_AHEAD = 3          # alert about subs expiring in N days


def _already_alerted_today(db: Session, alert_type: str) -> bool:
    """Check if we already fired this alert type today (avoid spam)."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(Alert)
        .filter(Alert.alert_type == alert_type, Alert.created_at >= today_start)
        .first()
    ) is not None


def run_alert_checks():
    """Main entry point — called by APScheduler."""
    db: Session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # ─── 1. Cost spike ───
        if not _already_alerted_today(db, "cost_spike"):
            daily_cost = float(
                db.query(func.coalesce(func.sum(ChatLog.cost_usd), 0.0))
                .filter(ChatLog.created_at >= today_start, ChatLog.response_type == "answered")
                .scalar() or 0
            )
            if daily_cost >= COST_DAILY_THRESHOLD_USD:
                db.add(Alert(
                    alert_type="cost_spike",
                    severity="critical",
                    title=f"Daily cost spike: ${daily_cost:.2f}",
                    message=f"Today's API cost (${daily_cost:.2f}) has exceeded the ${COST_DAILY_THRESHOLD_USD:.2f} threshold.",
                    details={"threshold": COST_DAILY_THRESHOLD_USD, "actual": daily_cost},
                ))

        # ─── 2. Usage spike ───
        if not _already_alerted_today(db, "usage_spike"):
            daily_messages = int(
                db.query(func.count(ChatLog.id))
                .filter(ChatLog.created_at >= today_start)
                .scalar() or 0
            )
            if daily_messages >= MESSAGES_DAILY_THRESHOLD:
                db.add(Alert(
                    alert_type="usage_spike",
                    severity="warning",
                    title=f"Usage spike: {daily_messages} messages today",
                    message=f"Message volume ({daily_messages}) exceeded the {MESSAGES_DAILY_THRESHOLD} threshold.",
                    details={"threshold": MESSAGES_DAILY_THRESHOLD, "actual": daily_messages},
                ))

        # ─── 3. High refusal rate ───
        if not _already_alerted_today(db, "high_refusal_rate"):
            total = int(
                db.query(func.count(ChatLog.id))
                .filter(ChatLog.created_at >= today_start)
                .scalar() or 0
            )
            if total >= 20:  # only alert if enough data
                refused = int(
                    db.query(func.count(ChatLog.id))
                    .filter(ChatLog.created_at >= today_start, ChatLog.response_type == "refused")
                    .scalar() or 0
                )
                rate = refused / total
                if rate >= REFUSAL_RATE_THRESHOLD:
                    db.add(Alert(
                        alert_type="high_refusal_rate",
                        severity="warning",
                        title=f"High refusal rate: {rate*100:.1f}%",
                        message=f"Refusal rate today ({rate*100:.1f}%) exceeded {REFUSAL_RATE_THRESHOLD*100:.0f}% threshold ({refused}/{total} messages).",
                        details={"threshold": REFUSAL_RATE_THRESHOLD, "actual": rate, "refused": refused, "total": total},
                    ))

        # ─── 4. Expiring subscriptions ───
        if not _already_alerted_today(db, "expired_subs"):
            cutoff = now + timedelta(days=EXPIRING_SUBS_DAYS_AHEAD)
            expiring_subs = (
                db.query(Subscription)
                .filter(
                    Subscription.status == "active",
                    Subscription.subscription_end.isnot(None),
                    Subscription.subscription_end <= cutoff,
                    Subscription.subscription_end >= now,
                )
                .all()
            )
            if expiring_subs:
                users_info = []
                for s in expiring_subs:
                    days_left = (s.subscription_end - now).days
                    users_info.append({
                        "whatsapp": s.whatsapp_number,
                        "plan": s.plan_name or "Unknown",
                        "expires": s.subscription_end.strftime("%Y-%m-%d") if s.subscription_end else "—",
                        "days_left": days_left,
                    })
                user_lines = ", ".join(
                    f"{u['whatsapp']} ({u['plan']}, {u['days_left']}d left)"
                    for u in users_info
                )
                db.add(Alert(
                    alert_type="expired_subs",
                    severity="info",
                    title=f"{len(expiring_subs)} subscription(s) expiring within {EXPIRING_SUBS_DAYS_AHEAD} days",
                    message=f"Expiring users: {user_lines}",
                    details={"count": len(expiring_subs), "days_ahead": EXPIRING_SUBS_DAYS_AHEAD, "users": users_info},
                ))

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"❌ Alert check failed: {e}")
    finally:
        db.close()
