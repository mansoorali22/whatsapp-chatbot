# Plug & Pay Webhook - Quick Setup

## 🚀 Quick Steps

### 1. Go to Plug & Pay Dashboard
- Log in to your account
- Find **Settings** → **Webhooks** (or **Integrations** → **Webhooks**)

### 2. Add Webhook

**Webhook URL:**
```
https://whatsapp-chatbot-ypib.onrender.com/plugpay/webhook
```

**Subscribe to Events:**
- ✅ Payment Received
- ✅ Subscription Created
- ✅ Subscription Updated
- ✅ Subscription Cancelled

### 3. Set Webhook Secret

1. Generate a secret (or use existing one)
2. Save it - you'll need it for Render

**Example:** `plugnpay_secret_abc123xyz789`

### 4. Add Secret to Render

1. Go to **Render Dashboard** → Your Service
2. **Environment** tab
3. Add variable:
   - **Key:** `PLUGNPAY_WEBHOOK_SECRET`
   - **Value:** (your secret from step 3)
4. **Save** → Auto-redeploys

### 5. Test

1. Create a test payment/subscription
2. Check Render logs for incoming webhook
3. Verify no errors

---

## ✅ Done!

Your webhook is now configured. Payment events will automatically be sent to your app.

---

**Need more details?** See `PLUGNPAY_WEBHOOK_SETUP.md` for full guide.
