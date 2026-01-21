WhatsApp AI Book Chatbot
Mode A: Strict Book-Only Q&A System
A WhatsApp chatbot that answers questions exclusively from "Eat like an athlete" book using RAG (Retrieval-Augmented Generation) with strict citation requirements.

📋 Project Overview
Objective
Launch a WhatsApp chatbot that:

✅ Answers questions ONLY from the book content
✅ Includes citations and section references
✅ Refuses to answer when content is not covered
✅ No hallucinations or general knowledge responses
✅ Access controlled via subscription (Plug & Pay)

Tech Stack

Backend: FastAPI (Python 3.11+)
Database: Neon PostgreSQL with pgvector
LLM: OpenAI GPT-4o-mini + text-embedding-3-small
WhatsApp: Meta Cloud API
Payment: Plug & Pay
Deployment: Railway (or any Python host)


🚀 Quick Start
1. Install Dependencies
bashpip install -r requirements.txt
2. Setup Environment
bashcp .env.example .env
# Edit .env with your credentials
Required variables:

DATABASE_URL - Neon PostgreSQL connection string ✅
OPENAI_API_KEY - OpenAI API key ✅
WHATSAPP_PHONE_NUMBER_ID - From Meta Developer Console
WHATSAPP_ACCESS_TOKEN - From Meta Developer Console
WEBHOOK_VERIFY_TOKEN - Your chosen secret

3. Initialize Database
bash# Option 1: Using psql
psql "YOUR_DATABASE_URL" -f db/schema.sql

# Option 2: Copy SQL content to Neon SQL Editor
4. Ingest Book
bashpython scripts/ingest_book.py path/to/eat_like_an_athlete.pdf
5. Run Server
bashpython -m app.main
6. Test Setup
bashpython scripts/test_setup.py

📁 Project Structure
whatsapp-chatbot/
├── app/
│   ├── main.py                    # FastAPI application
│   ├── config.py                  # Configuration management
│   ├── logger.py                  # Logging setup
│   ├── routes/
│   │   ├── whatsapp.py           # WhatsApp webhook endpoints
│   │   └── plugnpay.py           # Plug & Pay webhook
│   ├── services/
│   │   ├── whatsapp_service.py   # WhatsApp API client
│   │   ├── subscription_service.py # Subscription management
│   │   └── rag_service.py        # RAG Q&A logic
│   └── models/
│       └── schemas.py            # Pydantic models
├── db/
│   ├── connection.py             # Database connection pool
│   └── schema.sql                # Database schema
├── scripts/
│   ├── ingest_book.py            # Book ingestion script
│   └── test_setup.py             # Setup verification
├── logs/                         # Application logs
├── .env                          # Environment variables (not in git)
├── .env.example                  # Environment template
├── requirements.txt              # Python dependencies
└── README.md                     # This file

🔧 Configuration
Environment Variables
VariableDescriptionExampleDATABASE_URLNeon PostgreSQL URLpostgresql://user:pass@host/dbWHATSAPP_PHONE_NUMBER_IDMeta Phone Number IDFrom Meta ConsoleWHATSAPP_ACCESS_TOKENMeta Access TokenFrom Meta ConsoleWEBHOOK_VERIFY_TOKENYour webhook secretMySecret123PLUGNPAY_WEBHOOK_SECRETPlug & Pay secretFrom Plug & PayOPENAI_API_KEYOpenAI API keysk-proj-...OPENAI_MODELLLM modelgpt-4o-miniDAILY_MESSAGE_LIMITMax messages per user/day50

🔌 WhatsApp Setup
Meta Developer Console

Create App:

Go to https://developers.facebook.com/apps/
Create App → Business → WhatsApp


Configure WhatsApp:

Add WhatsApp product
Note Phone Number ID
Generate Access Token


Setup Webhook:

Callback URL: https://your-domain.com/webhook/whatsapp
Verify Token: (your chosen secret)
Subscribe to: messages



Local Testing with ngrok
bash# Start ngrok
ngrok http 8000

# Use ngrok URL in Meta webhook settings
https://abc123.ngrok.io/webhook/whatsapp

📊 Database Schema
Tables

subscriptions - User access control

WhatsApp number (E.164 format)
Subscription status (active/expired/blocked)
Message count tracking


book_chunks - Book content with embeddings

Chunk text
Chapter/section metadata
Vector embeddings (1536 dimensions)


chat_logs - Interaction history

User messages
Bot responses
Response type (answered/refused/error)
Token usage




🤖 How It Works
1. Message Flow
User sends WhatsApp message
    ↓
Meta forwards to webhook
    ↓
Check subscription status
    ↓
Check rate limit
    ↓
RAG Service:
  - Generate query embedding
  - Search similar chunks (vector similarity)
  - Check similarity threshold
  - Generate answer with GPT-4
  - Verify grounding
    ↓
Send response
    ↓
Log interaction
2. RAG Pipeline

Retrieval:

Convert question to embedding
Find top-K similar chunks (cosine similarity)
Filter by similarity threshold (0.7)


Answer Generation:

Build context from retrieved chunks
Prompt GPT-4 with strict instructions
Generate answer with citations
Verify answer is grounded


Refusal Logic:

If similarity < threshold → refuse
If answer contains refusal phrases → refuse
If no chunks found → refuse



3. Citation Format
[1] Chapter: Nutrition Basics | Section: Macronutrients | Page 12
[2] Chapter: Performance Diet | Page 45

🧪 Testing
Run Verification Script
bashpython scripts/test_setup.py
Checks:

✅ All imports working
✅ Configuration complete
✅ Database connection
✅ pgvector extension
✅ Tables created
✅ OpenAI API working
✅ Book chunks ingested

Manual Testing
bash# Health check
curl http://localhost:8000/health

# Send test webhook
curl "http://localhost:8000/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=test123"

📈 Monitoring & Logs
Log Files

logs/app_YYYY-MM-DD.log - All logs
logs/errors_YYYY-MM-DD.log - Errors only

Usage Statistics
sql-- Daily usage
SELECT * FROM daily_usage_stats;

-- User stats
SELECT 
    whatsapp_number,
    COUNT(*) as total_messages,
    COUNT(*) FILTER (WHERE response_type = 'answered') as answered,
    COUNT(*) FILTER (WHERE response_type = 'refused') as refused
FROM chat_logs
GROUP BY whatsapp_number;

🚀 Deployment
Railway

Connect GitHub repo
Add environment variables
Railway auto-deploys
Update Meta webhook URL

Other Platforms

Render
Fly.io
DigitalOcean App Platform
Any Docker-compatible host


📝 Milestones
Milestone 1 - Setup & Foundation ✅

 Project skeleton
 Database schema
 WhatsApp webhook
 Plug & Pay webhook
 Book ingestion pipeline

Milestone 2 - Working MVP (Current)

 End-to-end Q&A flow
 Strict Mode A behavior
 Citations included
 Refusal logic
 Basic logging

Milestone 3 - Testing & Handover

 User acceptance testing
 Bug fixes
 Documentation
 Production deployment
 Handover complete


🐛 Troubleshooting
Database Connection Failed
bash# Test connection
psql "YOUR_DATABASE_URL"

# Check pgvector
SELECT * FROM pg_extension WHERE extname = 'vector';
WhatsApp Not Receiving Messages

✅ Check ngrok/public URL is accessible
✅ Verify webhook token matches
✅ Ensure webhook subscribed to "messages"
✅ Check logs for errors

OpenAI API Errors

✅ Verify API key is valid
✅ Check billing/credits
✅ Monitor rate limits

No Book Chunks Found
bash# Re-run ingestion
python scripts/ingest_book.py path/to/book.pdf

# Check database
SELECT COUNT(*) FROM book_chunks;

📞 Support
For issues or questions:

Check logs in logs/ directory
Run python scripts/test_setup.py
Verify environment variables
Check database connectivity