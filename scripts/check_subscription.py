"""
Check if a WhatsApp number has a subscription (e.g. after a Plug&Pay payment).

Usage:
  python scripts/check_subscription.py 03368231166
  python scripts/check_subscription.py +923368231166
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.connection import SessionLocal, init_db
from app.db.models import Subscription


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/check_subscription.py <whatsapp_number>")
        sys.exit(1)
    raw = sys.argv[1].strip()
    digits = "".join(c for c in raw if c.isdigit())
    # Try several forms: as-is, +digits, and 0336... -> +92336...
    candidates = [raw]
    if digits:
        candidates.append("+" + digits)
        if digits.startswith("0") and len(digits) >= 10:
            candidates.append("+92" + digits[1:])  # Pakistan 0 -> 92
        candidates.append(digits)

    init_db()
    db = SessionLocal()
    try:
        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            sub = db.query(Subscription).filter(Subscription.whatsapp_number == candidate).first()
            if sub:
                print(f"Found subscription for {candidate}:")
                print(f"  status: {sub.status}")
                print(f"  plan_name: {sub.plan_name}")
                print(f"  credits: {sub.credits}")
                print(f"  total_purchased: {sub.total_purchased}")
                print(f"  created_at: {sub.created_at}")
                print(f"  updated_at: {sub.updated_at}")
                return
        # Try LIKE for partial (e.g. 68231166)
        subs = db.query(Subscription).filter(Subscription.whatsapp_number.like(f"%{digits[-8:]}%")).all()
        if subs:
            print("No exact match; possible rows (last 8 digits):")
            for s in subs:
                print(f"  {s.whatsapp_number} -> status={s.status} credits={s.credits}")
            return
        print(f"No subscription found for {raw} (tried: {', '.join(candidates)})")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
