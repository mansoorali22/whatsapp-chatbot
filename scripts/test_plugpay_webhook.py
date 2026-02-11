"""
Test the Plug&Pay webhook without making a real payment.

Usage:
  # Test against local server (run app first: uvicorn app.main:app --port 10000)
  python scripts/test_plugpay_webhook.py

  # Test against Render
  python scripts/test_plugpay_webhook.py https://whatsapp-chatbot-1-adqn.onrender.com

  # Custom WhatsApp number and credits
  python scripts/test_plugpay_webhook.py http://localhost:10000 31612345678 50
"""
import os
import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)

from app.core.config import settings


def main():
    base = (sys.argv[1] or "http://localhost:10000").rstrip("/")
    whatsapp = sys.argv[2] if len(sys.argv) > 2 else "31612345678"
    credits = int(sys.argv[3]) if len(sys.argv) > 3 else 50

    token = getattr(settings, "PLUG_N_PAY_TOKEN", None) or os.environ.get("PLUG_N_PAY_TOKEN")
    if not token:
        print("Set PLUG_N_PAY_TOKEN in .env or environment")
        sys.exit(1)

    url = f"{base}/plugpay/webhook"
    # Minimal payload that matches what the webhook expects
    payload = {
        "type": "new_simple_sale",
        "verify_token": token,
        "data": {
            "whatsapp_number": whatsapp,
            "credits": credits,
            "plan_name": "atleet-buddy-credits-50",
        },
    }
    headers = {"Content-Type": "application/json", "X-Webhook-Token": token}

    print(f"POST {url}")
    print(f"Body: {json.dumps(payload, indent=2)}")
    r = requests.post(url, json=payload, headers=headers, timeout=15)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")
    if r.status_code == 200:
        print("OK – check your Subscription table for this WhatsApp number.")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
