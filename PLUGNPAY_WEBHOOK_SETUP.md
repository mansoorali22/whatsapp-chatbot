# Plug & Pay Webhook Setup Guide

## 📋 Overview

This guide will help you configure the Plug & Pay webhook to send payment events to your WhatsApp chatbot application.

**Your Webhook URL:**
```
https://whatsapp-chatbot-ypib.onrender.com/plugpay/webhook
```

---

## 🎯 What You'll Need

1. **Plug & Pay Account** (admin access)
2. **Webhook URL** (provided above)
3. **Webhook Secret** (you'll get this from Plug & Pay or set it yourself)

---

## 📝 Step-by-Step Instructions

### Step 1: Log into Plug & Pay Dashboard

1. Go to your Plug & Pay dashboard
2. Log in with your admin credentials
3. Navigate to your account settings or webhook configuration section

### Step 2: Find Webhook Settings

The location may vary depending on your Plug & Pay dashboard version. Look for:

- **Settings** → **Webhooks**
- **Integrations** → **Webhooks**
- **API** → **Webhooks**
- **Developer** → **Webhooks**

If you can't find it, check the Plug & Pay documentation or contact their support.

### Step 3: Add New Webhook

1. Click **"Add Webhook"** or **"Create Webhook"** button
2. You'll see a form to configure the webhook

### Step 4: Configure Webhook Details

Fill in the following information:

#### **Webhook URL:**
```
https://whatsapp-chatbot-ypib.onrender.com/plugpay/webhook
```

**Important:** 
- Use HTTPS (not HTTP)
- Copy the URL exactly as shown above
- No trailing slash at the end

#### **Webhook Events to Subscribe To:**

Select the events you want to receive. For subscription management, you'll typically need:

- ✅ **Payment Received** / **Payment Success**
- ✅ **Subscription Created**
- ✅ **Subscription Updated**
- ✅ **Subscription Cancelled**
- ✅ **Payment Failed** (optional, but recommended)

**Note:** The exact event names may vary in Plug & Pay. Look for events related to:
- Payments
- Subscriptions
- Customer updates

#### **Webhook Secret (Optional but Recommended):**

1. Generate a secure random string (or use one you already have)
2. This secret will be used to verify webhook requests
3. **Save this secret** - you'll need to add it to Render environment variables

**Example secret format:**
```
plugnpay_webhook_secret_abc123xyz789
```

**How to generate a secure secret:**
- Use an online generator: https://www.random.org/strings/
- Or use this command: `openssl rand -hex 32`
- Make it at least 32 characters long

### Step 5: Save Webhook Configuration

1. Review all settings
2. Click **"Save"** or **"Create Webhook"**
3. Plug & Pay will attempt to send a test webhook (if available)

### Step 6: Test Webhook (If Available)

Some Plug & Pay dashboards allow you to:
1. Click **"Test Webhook"** or **"Send Test Event"**
2. Check if your application receives the test event
3. Verify the response is successful

### Step 7: Add Webhook Secret to Render

1. Go to **Render Dashboard**: https://dashboard.render.com
2. Select your service: **whatsapp-chatbot-ypib**
3. Go to **Environment** tab
4. Add or update this environment variable:

   **Key:** `PLUGNPAY_WEBHOOK_SECRET`
   
   **Value:** (The secret you set in Step 4)
   
   Example:
   ```
   PLUGNPAY_WEBHOOK_SECRET=plugnpay_webhook_secret_abc123xyz789
   ```

5. Click **"Save Changes"**
6. Render will automatically redeploy

---

## 🔍 Verification Steps

### 1. Check Webhook is Active

In Plug & Pay dashboard:
- Webhook should show status: **"Active"** or **"Enabled"**
- Last delivery status should be successful (after first real event)

### 2. Test with a Real Transaction

1. Create a test subscription/payment in Plug & Pay
2. Check Render logs to see if webhook was received:
   - Go to Render Dashboard → Your Service → **Logs**
   - Look for incoming POST requests to `/plugpay/webhook`

### 3. Check Application Logs

In Render logs, you should see:
- ✅ Incoming webhook requests
- ✅ Payment/subscription events being processed
- ✅ No authentication errors

---

## 🐛 Troubleshooting

### Issue: Webhook Not Receiving Events

**Possible Causes:**
1. **Webhook URL is incorrect**
   - ✅ Verify URL is exactly: `https://whatsapp-chatbot-ypib.onrender.com/plugpay/webhook`
   - ✅ Check for typos
   - ✅ Ensure HTTPS (not HTTP)

2. **Webhook is not active**
   - ✅ Check Plug & Pay dashboard - webhook should be "Active"
   - ✅ Verify events are subscribed

3. **Application not responding**
   - ✅ Check Render logs for errors
   - ✅ Verify service is "Live" in Render dashboard

### Issue: Authentication Errors

**If you see "Unauthorized" or "Invalid Signature":**

1. **Verify Webhook Secret:**
   - Check `PLUGNPAY_WEBHOOK_SECRET` in Render environment variables
   - Ensure it matches the secret in Plug & Pay dashboard
   - No extra spaces or quotes

2. **Check Secret Format:**
   - Should be a string (no special encoding needed)
   - Copy-paste directly (don't type manually)

### Issue: 404 Not Found

**If Plug & Pay gets 404 response:**

1. **Verify endpoint exists:**
   - Check: https://whatsapp-chatbot-ypib.onrender.com/plugpay/webhook
   - Should return a response (even if it's an error about missing data)

2. **Check route is registered:**
   - The endpoint should be available
   - If you see 404, the route might not be implemented yet (this is expected for MVP)

### Issue: Timeout Errors

**If Plug & Pay reports timeouts:**

1. **Check Render service:**
   - Ensure service is running (not sleeping)
   - Free tier services may sleep after inactivity
   - Consider upgrading if needed

2. **Response time:**
   - Webhook handler should respond quickly (< 5 seconds)
   - If processing takes longer, return 200 OK immediately and process async

---

## 📊 Webhook Event Format

Plug & Pay typically sends webhooks in JSON format. Example structure:

```json
{
  "event": "payment.received",
  "data": {
    "transaction_id": "txn_123456",
    "amount": 29.99,
    "currency": "EUR",
    "customer_id": "cust_123",
    "whatsapp_number": "+31612345678",
    "subscription_id": "sub_123",
    "status": "completed",
    "timestamp": "2026-01-26T16:30:00Z"
  }
}
```

**Note:** Actual format may vary. Check Plug & Pay documentation for exact structure.

---

## 🔒 Security Best Practices

1. **Use HTTPS Only:**
   - ✅ Webhook URL must use HTTPS
   - ✅ Render provides SSL automatically

2. **Verify Webhook Signature:**
   - ✅ Always verify webhook secret
   - ✅ Don't trust requests without verification

3. **Validate Data:**
   - ✅ Check all required fields are present
   - ✅ Validate amounts, IDs, etc.

4. **Idempotency:**
   - ✅ Handle duplicate webhook deliveries
   - ✅ Use transaction IDs to prevent duplicate processing

---

## 📞 Support

If you encounter issues:

1. **Check Plug & Pay Documentation:**
   - Look for webhook setup guides
   - Check API documentation

2. **Check Render Logs:**
   - Go to Render Dashboard → Logs
   - Look for error messages

3. **Contact Support:**
   - Plug & Pay support (for webhook configuration issues)
   - Development team (for application issues)

---

## ✅ Checklist

Before considering setup complete:

- [ ] Webhook URL added in Plug & Pay dashboard
- [ ] Webhook is set to "Active" status
- [ ] Required events are subscribed
- [ ] Webhook secret is set in Plug & Pay
- [ ] `PLUGNPAY_WEBHOOK_SECRET` added to Render environment variables
- [ ] Test webhook sent (if available)
- [ ] Render logs show webhook received (after test/real event)
- [ ] No errors in application logs

---

## 🎯 Quick Reference

**Webhook URL:**
```
https://whatsapp-chatbot-ypib.onrender.com/plugpay/webhook
```

**Environment Variable in Render:**
```
PLUGNPAY_WEBHOOK_SECRET=your_secret_here
```

**Test the Endpoint:**
```bash
curl -X POST https://whatsapp-chatbot-ypib.onrender.com/plugpay/webhook \
  -H "Content-Type: application/json" \
  -d '{"test": "data"}'
```

---

*Last Updated: Based on current deployment setup*
