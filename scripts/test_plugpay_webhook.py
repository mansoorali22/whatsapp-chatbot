"""
Test script for Plug & Pay webhook.
Run the app first (uvicorn app.main:app), then run this script.
Or use the curl commands in VERIFY_PLUGPAY_WEBHOOK.md against localhost or your Render URL.
"""
import sys
import requests

# Change to your base URL (local or Render)
BASE_URL = "http://127.0.0.1:8000"
# BASE_URL = "https://whatsapp-chatbot-ypib.onrender.com"

WEBHOOK_URL = f"{BASE_URL}/plugpay/webhook"


def test_flat_payload():
    """Minimal flat payload - just whatsapp_number."""
    payload = {
        "type": "payment_received",
        "whatsapp_number": "+31612345678",
        "credits": 20,
        "plan_name": "Buddy Pro",
    }
    print("Sending flat payload:", payload)
    r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    print(f"Status: {r.status_code}, Response: {r.json()}")
    return r.status_code == 200


def test_plug_and_pay_style():
    """Payload shaped like Plug & Pay (type + data with order/customer/custom_fields)."""
    payload = {
        "type": "new_simple_sale",
        "data": {
            "order": {
                "custom_fields": {
                    "whatsapp_number": "+31687654321",
                    "credits": 15,
                    "plan_name": "Buddy Start",
                },
            },
            "customer": {
                "id": "cust_plugpay_123",
            },
        },
    }
    print("Sending Plug & Pay style payload")
    r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    print(f"Status: {r.status_code}, Response: {r.json()}")
    return r.status_code == 200


def test_subscription_cancelled():
    """Test cancellation event."""
    payload = {
        "event_type": "subscription_cancelled",
        "whatsapp_number": "+31687654321",
    }
    print("Sending subscription_cancelled payload")
    r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    print(f"Status: {r.status_code}, Response: {r.json()}")
    return r.status_code == 200


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--render":
        BASE_URL = "https://whatsapp-chatbot-ypib.onrender.com"
        WEBHOOK_URL = f"{BASE_URL}/plugpay/webhook"
        print(f"Using Render URL: {WEBHOOK_URL}")

    print("--- Test 1: Flat payload (create/update subscription) ---")
    test_flat_payload()
    print()
    print("--- Test 2: Plug & Pay style payload ---")
    test_plug_and_pay_style()
    print()
    print("--- Test 3: Subscription cancelled ---")
    test_subscription_cancelled()
    print()
    print("Done. Check your database: SELECT * FROM subscriptions;")
