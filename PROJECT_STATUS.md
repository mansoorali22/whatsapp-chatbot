# Project Status & Explanation

## 📋 Overview
This document explains every file and concept implemented so far, and identifies what remains to complete **Milestone 1**.

---

## ✅ What Has Been Implemented

### 1. **Project Structure** (`app/` directory)
The project follows a clean FastAPI architecture with separation of concerns:

```
app/
├── main.py              # FastAPI application entry point
├── core/
│   └── config.py        # Configuration management
├── db/
│   ├── connection.py    # Database connection & initialization
│   └── models.py        # SQLAlchemy models
├── api/
│   ├── whatsapp.py      # WhatsApp webhook router (skeleton)
│   └── plugnpay.py      # Plug & Pay webhook router (skeleton)
├── services/
│   ├── rag.py           # RAG service (empty)
│   └── payment_logic.py # Payment logic (empty)
└── utils/
    └── logger.py        # Logging configuration
```

---

### 2. **Core Application** (`app/main.py`)

**Purpose**: FastAPI application entry point

**What it does**:
- Creates FastAPI app instance
- Sets up CORS middleware (allows cross-origin requests)
- Includes routers for WhatsApp and Plug & Pay endpoints
- Initializes database on startup
- Sets up logging on startup

**Key Features**:
- ✅ FastAPI app created
- ✅ CORS configured
- ✅ Routers registered (though endpoints not implemented yet)
- ✅ Database initialization on startup
- ✅ Logging setup

---

### 3. **Configuration Management** (`app/core/config.py`)

**Purpose**: Centralized configuration using Pydantic Settings

**What it does**:
- Loads environment variables from `.env` file
- Validates configuration values
- Provides type-safe access to settings

**Configuration Categories**:
1. **Database**: `DATABASE_URL`
2. **WhatsApp**: 
   - `WHATSAPP_PHONE_NUMBER_ID`
   - `WHATSAPP_ACCESS_TOKEN` (aliased as `WHATSAPP_TOKEN`)
   - `WEBHOOK_VERIFY_TOKEN`
   - `WHATSAPP_API_VERSION` (default: v21.0)
3. **Plug & Pay**: `PLUGNPAY_WEBHOOK_SECRET` (aliased as `PLUG_PAY_SECRET`)
4. **OpenAI**:
   - `OPENAI_API_KEY`
   - `OPENAI_MODEL` (default: gpt-4o-mini)
   - `OPENAI_EMBEDDING_MODEL` (default: text-embedding-3-small)
   - `EMBEDDING_DIMENSION` (default: 1536)
5. **App Config**:
   - `ENVIRONMENT` (default: development)
   - `LOG_LEVEL` (default: INFO)
   - `DAILY_MESSAGE_LIMIT` (default: 50)
6. **RAG Settings**:
   - `RETRIEVAL_TOP_K` (default: 5)
   - `SIMILARITY_THRESHOLD` (default: 0.7)
   - `MAX_TOKENS_RESPONSE` (default: 500)
7. **Book Info**:
   - `BOOK_TITLE` (default: "Eat like an athlete")
   - `DEFAULT_LANGUAGE` (default: "English")

**Status**: ✅ Complete and well-structured

---

### 4. **Database Models** (`app/db/models.py`)

**Purpose**: SQLAlchemy ORM models for database tables

**Tables Defined**:

#### a) **Subscription** Table
Tracks user subscriptions and access control:
- `id`: Primary key
- `whatsapp_number`: Unique WhatsApp number (E.164 format)
- `status`: Subscription status (active/expired/blocked)
- `plan_name`: Subscription plan name
- `is_recurring`: Whether subscription is recurring
- `plugnpay_customer_id`: Plug & Pay customer identifier
- `credits`: Current credit balance (default: 15 for trial)
- `total_purchased`: Lifetime credits purchased
- `message_count`: Total questions asked
- `is_trial`: Whether user is on trial
- `subscription_start`: Subscription start date
- `subscription_end`: Subscription expiry date
- `created_at`, `updated_at`: Timestamps

**Purpose**: Access control - only paying users can use the bot

#### b) **ProcessedMessage** Table
Prevents duplicate message processing:
- `message_id`: WhatsApp message ID (wamid) as primary key
- `created_at`: Timestamp

**Purpose**: Idempotency - ensures each WhatsApp message is processed only once

#### c) **BookChunk** Table
Stores book content with vector embeddings:
- `id`: Primary key
- `content`: Text content of the chunk
- `embedding`: Vector embedding (1536 dimensions for OpenAI text-embedding-3-small)
- `metadata_json`: JSON field storing chapter, section, page info

**Purpose**: RAG retrieval - stores book chunks with embeddings for similarity search

**Note**: Has a high-performance HNSW index on the embedding column for fast vector similarity search

#### d) **ChatLog** Table
Logs all user interactions:
- `id`: Primary key
- `whatsapp_number`: User's WhatsApp number
- `user_message`: User's question
- `bot_response`: Bot's answer
- `response_type`: Type of response (answered/refused/error)
- `chunks_used`: JSON array of chunk IDs used in the answer
- `created_at`: Timestamp

**Purpose**: Analytics and debugging - tracks all Q&A interactions

**Status**: ✅ Complete with proper indexes

---

### 5. **Database Connection** (`app/db/connection.py`)

**Purpose**: Database connection management and initialization

**What it does**:
1. Creates SQLAlchemy engine from `DATABASE_URL`
2. Creates session factory (`SessionLocal`)
3. Defines `Base` class for ORM models
4. `init_db()` function:
   - Creates pgvector extension (required for vector operations)
   - Creates all tables defined in models
5. `get_db()` function: Dependency injection for FastAPI routes

**Key Features**:
- ✅ pgvector extension creation
- ✅ Automatic table creation
- ✅ Session management for FastAPI

**Status**: ✅ Complete

---

### 6. **Logging System** (`app/utils/logger.py`)

**Purpose**: Centralized logging configuration

**What it does**:
- Sets up structured logging with multiple handlers:
  - **Console handler**: Outputs to stdout with simple format
  - **File handler**: Detailed logs to `app/utils/app_YYYY-MM-DD.log`
  - **Error handler**: Errors only to `app/utils/errors_YYYY-MM-DD.log`
- Configures log levels
- Suppresses noisy third-party loggers (httpx, openai, etc.)

**Log Format**:
- Console: Simple format with timestamp, level, message
- File: Detailed format with function name, line number, etc.

**Status**: ✅ Complete and production-ready

---

### 7. **Book Ingestion Script** (`scripts/ingest_book.py`)

**Purpose**: Extracts book content, chunks it, generates embeddings, and stores in database

**What it does**:
1. Loads DOCX file using `Docx2txtLoader` (LangChain)
2. Splits document into chunks using `RecursiveCharacterTextSplitter`:
   - Chunk size: 1000 characters
   - Overlap: 150 characters
   - Separators: `\n\n`, `\n`, `.`, ` `, `""`
3. Generates embeddings using OpenAI `text-embedding-3-small` model
4. Stores chunks in `book_chunks` table with:
   - Content text
   - 1536-dimensional vector embedding
   - Metadata (book title, chunk index)

**Current Issues**:
- ⚠️ **Hardcoded file path** (line 91): Needs to accept command-line argument
- ⚠️ **No error handling** for file reading
- ⚠️ **No progress bar** for large books
- ⚠️ **Metadata is minimal** (no chapter/section extraction from DOCX)

**Status**: ✅ Core functionality works, but needs improvements

---

### 8. **API Routers** (Skeleton Only)

#### a) **WhatsApp Router** (`app/api/whatsapp.py`)
**Current State**: Empty router, no endpoints implemented

**Needs**:
- Webhook verification endpoint (GET)
- Message receiving endpoint (POST)
- WhatsApp API client to send messages

#### b) **Plug & Pay Router** (`app/api/plugnpay.py`)
**Current State**: Empty router, no endpoints implemented

**Needs**:
- Webhook endpoint to receive payment events
- Subscription creation/update logic

---

### 9. **Services** (Empty)

#### a) **RAG Service** (`app/services/rag.py`)
**Current State**: Empty file

**Needs**:
- Query embedding generation
- Vector similarity search
- Answer generation with citations
- Refusal logic when no relevant content found

#### b) **Payment Logic** (`app/services/payment_logic.py`)
**Current State**: Empty file

**Needs**:
- Subscription verification
- Credit management
- Rate limiting logic

---

### 10. **Dependencies** (`requirements.txt`)

**Status**: ✅ Complete with all necessary packages:
- FastAPI, Uvicorn (web framework)
- SQLAlchemy, psycopg2-binary, pgvector (database)
- LangChain, OpenAI (RAG pipeline)
- Pydantic, python-dotenv (configuration)
- And all dependencies

---

## ❌ What's Missing for Milestone 1

### Milestone 1 Requirements:
1. ✅ Repo + project skeleton → **DONE**
2. ✅ Hosting + DB created → **DONE** (structure ready)
3. ❌ WhatsApp Cloud API webhook reachable (test ping) → **MISSING**
4. ❌ Plug & Pay webhook endpoint created (test event accepted) → **MISSING**
5. ⚠️ Book ingestion pipeline prepared (first extraction run) → **PARTIAL** (works but needs fixes)

---

### 1. **WhatsApp Webhook Implementation** (`app/api/whatsapp.py`)

**Required Endpoints**:

#### a) **Webhook Verification** (GET `/whatsapp/webhook`)
Meta sends a verification request when you set up the webhook. Must:
- Accept `hub.mode`, `hub.verify_token`, `hub.challenge` query params
- Verify `hub.verify_token` matches `settings.WEBHOOK_VERIFY_TOKEN`
- Return `hub.challenge` if valid, 403 if invalid

#### b) **Message Handler** (POST `/whatsapp/webhook`)
Receives incoming WhatsApp messages. Must:
- Parse Meta webhook payload
- Extract user phone number (E.164 format)
- Extract message text
- Check if message already processed (using `ProcessedMessage` table)
- Verify subscription status
- Call RAG service to generate answer
- Send reply via WhatsApp API
- Log interaction in `ChatLog` table

#### c) **WhatsApp API Client**
Function to send messages via Meta WhatsApp Cloud API:
- Endpoint: `https://graph.facebook.com/{version}/{phone-number-id}/messages`
- Headers: Authorization with access token
- Payload: JSON with recipient, message type, content

**Status**: ❌ **NOT IMPLEMENTED**

---

### 2. **Plug & Pay Webhook Implementation** (`app/api/plugnpay.py`)

**Required Endpoint**:

#### **Payment Webhook** (POST `/plugpay/webhook`)
Receives payment events from Plug & Pay. Must:
- Verify webhook signature (using `PLUGNPAY_WEBHOOK_SECRET`)
- Parse payment event (subscription created, payment received, etc.)
- Extract WhatsApp phone number from checkout data
- Create or update `Subscription` record:
  - Set status to "active"
  - Set subscription dates
  - Set credits based on plan
  - Store Plug & Pay customer ID
- Return 200 OK

**Status**: ❌ **NOT IMPLEMENTED**

#### **Option A: Fetch order by ID when webhook has no phone**
PlugAndPay webhooks sometimes only send `event` (e.g. `trigger_type`, `triggerable_id`, `triggerable_type`) and no customer/phone. The app fetches the order from the PlugAndPay API and reads the phone from the response.
- **API base URL**: `https://api.plugandpay.com` (from [plug-and-pay/sdk-php](https://github.com/plug-and-pay/sdk-php) and [docs.plugandpay.nl](https://docs.plugandpay.nl/docs/plug-pay/api)).
- **Endpoint**: `GET /v2/orders/{triggerable_id}` (per SDK `OrderService::find()`).
- **Auth**: `Authorization: Bearer {PLUG_N_PAY_TOKEN}` (same token as webhook config).
- **Env on Render**: Set **`PLUG_N_PAY_API_URL`** = `https://api.plugandpay.com` (optional; this is the default). Keep **`PLUG_N_PAY_TOKEN`** set.

---

### 3. **RAG Service Implementation** (`app/services/rag.py`)

**Required Functions**:

#### a) **`generate_answer(question: str, db: Session) -> dict`**
Main RAG function:
1. Generate query embedding using OpenAI
2. Search for similar chunks using vector similarity (pgvector)
3. Filter by similarity threshold (default: 0.7)
4. If no chunks found or similarity too low → return refusal
5. Build context from retrieved chunks
6. Generate answer using GPT-4 with strict prompt:
   - Only answer from provided context
   - Include citations (chapter/section/page)
   - Refuse if not in context
7. Return answer with metadata (chunks used, citations)

#### b) **`search_similar_chunks(embedding: list, db: Session, top_k: int) -> list`**
Vector similarity search:
- Use pgvector cosine similarity
- Return top-K most similar chunks
- Include similarity scores

**Status**: ❌ **NOT IMPLEMENTED**

---

### 4. **Payment/Subscription Logic** (`app/services/payment_logic.py`)

**Required Functions**:

#### a) **`verify_subscription(whatsapp_number: str, db: Session) -> bool`**
Checks if user has active subscription:
- Query `Subscription` table
- Check `status == "active"`
- Check `subscription_end` is in future (if applicable)
- Check `credits > 0` (if credit-based)
- Return True/False

#### b) **`check_rate_limit(whatsapp_number: str, db: Session) -> bool`**
Checks daily message limit:
- Query `ChatLog` for today's messages
- Compare with `settings.DAILY_MESSAGE_LIMIT`
- Return True if under limit

#### c) **`deduct_credit(whatsapp_number: str, db: Session)`**
Deducts credit after message:
- Decrement `credits` in `Subscription` table
- Increment `message_count`

**Status**: ❌ **NOT IMPLEMENTED**

---

### 5. **Book Ingestion Script Improvements** (`scripts/ingest_book.py`)

**Required Fixes**:
- ❌ Accept file path as command-line argument (not hardcoded)
- ❌ Better error handling
- ❌ Progress bar for large books
- ⚠️ Extract chapter/section metadata from DOCX (if available)

**Status**: ⚠️ **PARTIAL** (works but needs fixes)

---

### 6. **Environment Variables Template** (`.env.example`)

**Required**: Create `.env.example` file with all required variables (without actual secrets)

**Status**: ❌ **MISSING**

---

### 7. **Testing & Verification**

**Required**:
- Test database connection
- Test pgvector extension
- Test webhook endpoints (with test requests)
- Test book ingestion

**Status**: ❌ **MISSING**

---

## 📊 Milestone 1 Completion Checklist

### Acceptance Criteria:
- [ ] Webhook endpoints live (WA + Plug & Pay)
- [ ] DB schema created (users/subscriptions/logs) → ✅ **DONE**
- [ ] Book can be ingested to create an initial index → ⚠️ **PARTIAL** (works but needs fixes)

### Remaining Tasks:

1. **Implement WhatsApp Webhook** (`app/api/whatsapp.py`)
   - [ ] GET endpoint for verification
   - [ ] POST endpoint for messages
   - [ ] WhatsApp API client function
   - [ ] Message processing logic

2. **Implement Plug & Pay Webhook** (`app/api/plugnpay.py`)
   - [ ] POST endpoint for payment events
   - [ ] Signature verification
   - [ ] Subscription creation/update logic

3. **Implement RAG Service** (`app/services/rag.py`)
   - [ ] Query embedding generation
   - [ ] Vector similarity search
   - [ ] Answer generation with citations
   - [ ] Refusal logic

4. **Implement Payment Logic** (`app/services/payment_logic.py`)
   - [ ] Subscription verification
   - [ ] Rate limiting
   - [ ] Credit management

5. **Fix Book Ingestion Script** (`scripts/ingest_book.py`)
   - [ ] Accept command-line argument for file path
   - [ ] Better error handling
   - [ ] Progress indication

6. **Create `.env.example`** file

7. **Test Everything**
   - [ ] Database connection
   - [ ] Webhook verification (WhatsApp)
   - [ ] Webhook verification (Plug & Pay)
   - [ ] Book ingestion
   - [ ] End-to-end message flow (when RAG is ready)

---

## 🎯 Summary

### What's Complete (✅):
- Project structure
- Database models and schema
- Configuration management
- Logging system
- Database connection with pgvector
- Basic book ingestion script (needs fixes)
- Dependencies

### What's Missing (❌):
- WhatsApp webhook endpoints (verification + message handling)
- Plug & Pay webhook endpoint
- RAG service (core Q&A logic)
- Payment/subscription verification logic
- WhatsApp API client
- Environment variables template
- Testing scripts

### Estimated Work Remaining:
- **WhatsApp Webhook**: 2-3 hours
- **Plug & Pay Webhook**: 1-2 hours
- **RAG Service**: 3-4 hours
- **Payment Logic**: 1-2 hours
- **Fixes & Testing**: 2-3 hours

**Total**: ~10-14 hours of development work

---

## 🚀 Next Steps

1. **Start with WhatsApp Webhook** (most critical)
2. **Implement RAG Service** (core functionality)
3. **Add Payment Logic** (access control)
4. **Implement Plug & Pay Webhook** (subscription management)
5. **Fix Book Ingestion Script** (improve usability)
6. **Create `.env.example`** (documentation)
7. **Test Everything** (verification)

---

*Last Updated: Based on current codebase analysis*
