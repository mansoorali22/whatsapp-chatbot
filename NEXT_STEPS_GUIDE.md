# Next Steps Guide – Detailed Instructions

This guide walks you through each step after your app is deployed on Render and env vars are set.

---

## Step 1: Verify the Plug & Pay Webhook (Test That Your Backend Accepts Events)

**What this does:** Sends fake “payment” and “subscription” events to your live app. If it works, you get HTTP 200 and new rows in the `subscriptions` table. This proves the webhook endpoint and payment logic work.

### 1.1 Run the test script with the token

Your Render app only accepts webhook requests that send the correct secret. The test script can send it if you set the same secret in your environment.

**On Windows (PowerShell):**

1. Open PowerShell.
2. Go to your project folder:
   ```powershell
   cd "c:\Users\everyday\Desktop\gig\RAG for book\whatsapp-chatbot"
   ```
3. Set the webhook secret (use the same value as in Render):
   ```powershell
   $env:PLUG_N_PAY_TOKEN = "Q48A9-LSBEZ-7562T-DMC3X"
   ```
4. Run the test against Render:
   ```powershell
   python scripts/test_plugpay_webhook.py --render
   ```

**What you should see:**

- Lines like: `Status: 200, Response: {'status': 'ok', 'event_type': '...'}`  
- If you see `403` or `Forbidden`, the token doesn’t match what’s in Render (check Render → Environment → `PLUG_N_PAY_TOKEN`).

### 1.2 Check the database

After a successful test, your app should have created or updated rows in the `subscriptions` table.

1. Log in to **Neon** (https://neon.tech) and open your project.
2. Open the **SQL Editor**.
3. Run:
   ```sql
   SELECT id, whatsapp_number, status, plan_name, credits, total_purchased, is_trial, created_at
   FROM subscriptions
   ORDER BY id DESC
   LIMIT 10;
   ```

**What you should see:**

- Rows for numbers like `+31612345678` and `+31687654321` (from the test script).
- `status = 'active'`, `plan_name` like "Buddy Pro" or "Buddy Start", `credits` > 0, `is_trial = false`.

If you see these, Step 1 is done: your Plug & Pay webhook and payment logic work.

---

## Step 2: Configure Plug & Pay (So Real Payments Send Events to Your App)

**What this does:** Tells Plug & Pay where to send payment/subscription events and with which secret. After this, real payments will call your Render URL and update the same `subscriptions` table.

### 2.1 Find webhook settings in Plug & Pay

1. Log in to your **Plug & Pay** account (dashboard / merchant area).
2. Look for one of these (wording may vary):
   - **Settings** → **Webhooks**
   - **Integrations** → **Webhooks**
   - **Developer** or **API** → **Webhooks**

If you can’t find it, check Plug & Pay’s help or docs for “webhook” or “notifications”.

### 2.2 Add a new webhook

1. Click **Add Webhook** / **Create Webhook** / **New Webhook**.
2. You’ll see a form. Fill it as below.

### 2.3 Webhook URL

- **Field name might be:** “Webhook URL”, “Callback URL”, “Endpoint URL”.
- **Enter exactly:**
  ```
  https://whatsapp-chatbot-ypib.onrender.com/plugpay/webhook
  ```
- Use **HTTPS** (not http).
- No space at the end. No trailing slash at the end.

### 2.4 Webhook secret / token

- **Field name might be:** “Webhook secret”, “Secret key”, “Verification token”, “Signing secret”.
- **Enter the same value** you have in Render as `PLUG_N_PAY_TOKEN`:
  ```
  Q48A9-LSBEZ-7562T-DMC3X
  ```
- Your app uses this to check that the request really comes from Plug & Pay. If Plug & Pay lets you *generate* a secret, generate one and then copy that **exact** value into Render → Environment → `PLUG_N_PAY_TOKEN` so they match.

### 2.5 Events to subscribe to

- You’ll see a list of events (e.g. “Payment received”, “Subscription created”).
- **Select at least:**
  - Payment received / sale completed / order paid (so you get credits when someone pays).
  - Subscription created (if you use subscriptions).
  - Subscription updated / renewed (if applicable).
  - Subscription cancelled (if applicable).

Names may differ (e.g. “new_simple_sale”, “payment.success”). Enable anything that means “a payment happened” or “a subscription was created/updated/cancelled”.

### 2.6 WhatsApp number in payments

- Your app needs the customer’s **WhatsApp number (E.164)** to create/update the right row in `subscriptions`.
- In Plug & Pay, at checkout or in the product/order setup, add a **custom field** (or “metadata”) for the WhatsApp number, e.g.:
  - Name: `whatsapp_number` or `whatsapp` or `phone`
  - Value: the customer’s number in E.164 (e.g. `+31612345678`).
- If Plug & Pay sends this in `custom_fields` or similar, our webhook code will read it and link the payment to that number.

### 2.7 Save and (if available) test

- Click **Save** / **Create**.
- If Plug & Pay has **“Send test webhook”** or **“Test”**, use it and check Render logs (see Step 4) to see the request and any errors.

After this, Step 2 is done: real payments will hit your app.

---

## Step 3: Configure the WhatsApp Webhook (So Meta Sends Messages to Your App)

**What this does:** Tells Meta (Facebook) to send every incoming WhatsApp message to your Render app. Your app then runs RAG and replies. Without this, the bot won’t receive any messages.

### 3.1 Open Meta Developer Console

1. Go to **https://developers.facebook.com/**
2. Log in and open the **app** that has WhatsApp (the one where you got `WHATSAPP_PHONE_ID` and `WHATSAPP_ACCESS_TOKEN`).

### 3.2 Open WhatsApp configuration

1. In the left sidebar, click **WhatsApp** (under “Products” or “Add Products”).
2. Click **Configuration** (or **Setup** / **API Setup**).

You’ll see a **Webhook** section with:
- **Callback URL**
- **Verify token**
- **Subscribe** button

### 3.3 Set Callback URL

- **Field:** “Callback URL” or “Webhook URL”.
- **Enter:**
  ```
  https://whatsapp-chatbot-ypib.onrender.com/whatsapp/get-messages
  ```
- Important: your app uses `/whatsapp/get-messages` for both **verification (GET)** and **receiving messages (POST)**. So this one URL is correct for both.
- Use **HTTPS**. No trailing slash.

### 3.4 Set Verify token

- **Field:** “Verify token” or “Webhook verify token”.
- **Enter exactly** the same value as in Render’s `WEBHOOK_VERIFY_TOKEN`:
  ```
  AtleetBuddy_2024
  ```
- When you click “Verify and save”, Meta sends a GET request with this token. Your app checks it and returns the challenge. If the token doesn’t match, verification fails.

### 3.5 Verify and save

1. Click **Verify and save** (or “Verify” then “Save”).
2. If something goes wrong:
   - **“Verification failed”** → Callback URL or verify token is wrong. Double-check URL (no typo, no trailing slash) and that Render has `WEBHOOK_VERIFY_TOKEN=AtleetBuddy_2024`.
   - **“URL not reachable”** → Render service might be sleeping (free tier) or down. Open the URL in a browser to wake it; wait a minute and try again.

### 3.6 Subscribe to messages

- After verification, you’ll see **“Webhook fields”** or **“Subscribe to”** with checkboxes.
- **Check:** **messages** (required for receiving incoming messages).
- You can also subscribe to “message_deliveries”, “message_reads” if you need them later. For the bot, **messages** is the one that matters.
- Save / confirm.

After this, Step 3 is done: WhatsApp will send messages to your app.

---

## Step 4: End-to-End Testing (Make Sure Everything Works Together)

**What this does:** Confirms that a real WhatsApp message gets a RAG reply, and (optionally) that a real payment creates/updates a subscription.

### 4.1 Test WhatsApp (user sends message, bot replies)

1. Open **WhatsApp** on your phone.
2. Start a chat with your **WhatsApp Business** number (the one linked to `WHATSAPP_PHONE_ID`).
3. Send a short message, e.g.:
   - “Hi”
   - Or a question about the book, e.g. “What does the book say about nutrition?”
4. **Expected:** Within a few seconds you get a reply (greeting or an answer from the book). If the app is on free tier and was sleeping, the first reply might take 30–60 seconds.
5. **If no reply:**
   - Check **Render** → your service → **Logs**. Look for errors when the message arrives.
   - Confirm in Meta Developer Console that the webhook is verified and “messages” is subscribed.
   - Confirm Render env vars: `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_ID`, `WEBHOOK_VERIFY_TOKEN`, `DATABASE_URL`, `OPENAI_API_KEY`.

### 4.2 Check Render logs

1. Go to **https://dashboard.render.com** → your service **whatsapp-chatbot-ypib**.
2. Open **Logs** (or “View logs”).
3. When you send a WhatsApp message, you should see lines like:
   - Incoming request to `/whatsapp/get-messages`
   - “NEW MESSAGE FROM …”
   - RAG or send-message activity (depending on how your app logs).

Errors (e.g. database, OpenAI, WhatsApp API) will show here and help you fix issues.

### 4.3 (Optional) Test a real Plug & Pay payment

1. Do a **test payment** (or real small payment) in your Plug & Pay checkout, and use a **WhatsApp number** in the custom field if you have one.
2. After the payment, check:
   - **Render logs:** You should see a POST to `/plugpay/webhook` and no 4xx/5xx errors.
   - **Neon:** Run the same `SELECT ... FROM subscriptions` query. You should see a new or updated row for that WhatsApp number with `status = 'active'` and credits added.

If both WhatsApp and (if you tested) payment work, end-to-end is working.

---

## Quick Checklist

| Step | What you did | How to confirm |
|------|----------------|-----------------|
| 1 | Run test script with `PLUG_N_PAY_TOKEN` against Render | HTTP 200, rows in `subscriptions` for test numbers |
| 2 | Configure Plug & Pay webhook URL + secret + events (+ WhatsApp in custom field) | Real payment triggers webhook; new/updated row in `subscriptions` |
| 3 | Set WhatsApp callback URL + verify token in Meta, subscribe to “messages” | “Verify and save” succeeds; messages appear in Render logs |
| 4 | Send a WhatsApp message and (optional) do a test payment | Bot replies; (optional) subscription row created/updated |

If you tell me which step you’re on (e.g. “Step 2 – Plug & Pay”) and what you see (e.g. “no webhook menu”, “verification failed”), I can give you exact clicks or fixes for that part.
