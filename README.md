# Automate This — SMB Automation Advisor

AI-powered chatbot backend that advises small businesses on which manual workflows to automate first and how to get started. Built with FastAPI, pgvector RAG, and OpenRouter LLMs.

## What It Does

- Accepts a natural-language description of a business problem
- Retrieves the most relevant automation patterns from a curated knowledge base (pgvector cosine similarity)
- Supplements retrieval with live documentation from n8n, Twenty CRM, Make, Zapier, and Lovable
- Returns a structured recommendation: what to automate, which tools to use, estimated ROI

## Tech Stack

| Layer | Choice |
|---|---|
| API | FastAPI (async) |
| Embeddings | `text-embedding-3-small` via OpenRouter (1536-dim) |
| Vector store | pgvector (PostgreSQL 16, ivfflat index) |
| ORM | SQLAlchemy async + asyncpg |
| Package manager | uv |
| Tests | pytest-asyncio (39 unit + 3 integration tests) |

## Project Structure

```
backend/
  app/
    main.py              # FastAPI app + /health + /admin/ingest endpoints
    config.py            # Pydantic settings (reads from .env)
    db.py                # Async SQLAlchemy engine + session factory
    rag/
      ingest.py          # Static KB ingestion — sha256 idempotency, pgvector upsert
      retriever.py       # Cosine similarity search (ivfflat, probes=10)
      live_ingester.py   # Fetch + chunk + embed live docs from 7 sources
  knowledge_base/        # 6 curated automation pattern markdown files
  tests/
    test_health.py       # /health + /admin/ingest endpoint tests
    test_ingest.py       # Unit tests for ingest, retriever, live ingester
    test_rag_integration.py  # Integration tests (requires live pgvector DB)
init.sql                 # DB schema + ivfflat index (runs on fresh Postgres volume)
docker-compose.yml       # Postgres 16 + pgvector for local dev
```

## Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Docker (for local Postgres) or a Postgres 16 instance with pgvector

### 1. Clone and install

```bash
git clone https://github.com/RomanMerg/rmerge-AE.2.5.git
cd rmerge-AE.2.5/backend
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your OpenRouter API key and database URL
```

Required variables:

| Variable | Description |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter key — get one at openrouter.ai/keys |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/dbname` |
| `ADMIN_API_KEY` | Random secret for the `/admin/ingest` endpoint |

### 3. Start Postgres

```bash
# From the repo root
docker compose up -d
```

This starts Postgres 16 with pgvector and runs `init.sql` to create the `documents` table and ivfflat index.

> **Existing Postgres?** If you already have a Postgres 16 instance with pgvector, create the database and run `init.sql` manually:
> ```sql
> CREATE DATABASE automate_this;
> \c automate_this
> CREATE EXTENSION IF NOT EXISTS vector;
> -- then paste contents of init.sql
> ```

### 4. Ingest the knowledge base

```bash
# Static KB (6 curated automation pattern files)
curl -X POST "http://localhost:8000/admin/ingest?source=static" \
  -H "X-Admin-Key: your_admin_key"

# Live docs from n8n, Twenty CRM, Make, Zapier, Lovable (optional)
curl -X POST "http://localhost:8000/admin/ingest?source=live" \
  -H "X-Admin-Key: your_admin_key"
```

### 5. Run the API

```bash
uv run uvicorn app.main:app --reload
```

API available at `http://localhost:8000`. Docs at `http://localhost:8000/docs`.

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | None | Uptime check |
| POST | `/admin/ingest` | `X-Admin-Key` | Ingest KB into pgvector |

Query params for `/admin/ingest`: `source=static` (default) or `source=live`.

## Running Tests

```bash
cd backend

# Unit tests only (no DB required)
uv run pytest -m "not integration"

# Integration tests (requires live Postgres with pgvector)
uv run pytest -m integration -v
```

## Knowledge Base

Six curated automation patterns in `backend/knowledge_base/`, each covering:

- **Lead Capture** — CRM auto-population from web forms
- **Email Parsing** — Extract structured data from inbound emails
- **Invoice Generation** — Auto-generate invoices from completed jobs
- **Appointment Booking** — Calendar sync and confirmation flows
- **Client Onboarding** — Welcome sequences and account provisioning
- **Social Media Scheduling** — Content queue and multi-platform posting

Each pattern includes manual time cost benchmarks, recommended tools, n8n node outlines, and ROI estimates.

## Schema Notes

The `documents` table has `UNIQUE` constraints on both `title` and `content_hash`. If you modify the schema after initial setup, run the migration manually:

```sql
-- If adding UNIQUE(title) to an existing DB:
ALTER TABLE documents ADD UNIQUE (title);
```
