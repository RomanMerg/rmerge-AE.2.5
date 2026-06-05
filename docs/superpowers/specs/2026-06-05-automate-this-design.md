# Design Spec: "Automate This" — SMB Automation Advisor

**Date:** 2026-06-05  
**Author:** Roman M  
**Status:** Approved — ready for implementation planning  
**Turing context:** Sprint 2 capstone (LangChain + RAG + tool calling)  
**Business context:** Foundation for a production AI automation consulting website

---

## 1. Purpose and Goals

A chatbot that small business owners use to ask "Can I automate X?" and receive a concrete plan, ROI estimate, and tool recommendation in under 2 minutes. Serves two audiences simultaneously:

- **Turing reviewer:** demonstrates RAG, tool calling, LangChain, OpenRouter, domain specialisation, security measures
- **Prospects/clients:** demonstrates Roman's expertise before they pay anything; acts as a lead capture and qualification tool

The app is built production-first: clean separation of concerns, environment-driven config, deployable to real infrastructure from day one.

---

## 2. Architecture Overview

```
Vercel (free)              Render.com ($0–7/mo)         Supabase (free tier)
┌──────────────────┐       ┌──────────────────────┐     ┌────────────────────┐
│  Next.js         │ POST  │  FastAPI             │     │  PostgreSQL        │
│  Landing page    │──────▶│  /chat               │────▶│  + pgvector        │
│  + Chat widget   │       │  /health             │     │  conversations     │
│                  │       │                      │     │  documents         │
└──────────────────┘       │  LangChain Agent     │     │  (RAG embeddings)  │
                           │  ├─ calculate_roi    │     └────────────────────┘
                           │  ├─ search_kb        │
                           │  └─ suggest_stack    │
                           └──────────────────────┘
                                      │
                               OpenRouter API
                               (gpt-4o-mini)
```

**Local development:** identical stack via Docker Compose — Ollama replaces OpenRouter for embeddings, local Postgres replaces Supabase. Two env var swaps, zero code changes.

---

## 3. Backend (FastAPI)

### 3.1 Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/chat` | none | Main conversation endpoint |
| GET | `/health` | none | Uptime check for Render |
| POST | `/admin/ingest` | API key header | Ingest knowledge base docs |

### 3.2 Request / Response

**POST /chat**
```json
// Request
{
  "session_id": "uuid-v4",
  "message": "I spend 4 hours a week copying leads from Gmail into a spreadsheet",
  "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
}

// Response
{
  "reply": "...",
  "tool_calls": ["search_kb", "calculate_roi"],
  "turn_number": 3,
  "tokens_used": 312,
  "cost_usd": 0.0004
}
```

### 3.3 Rate Limiting and Security

- **8 turns per session_id** enforced in DB (not just frontend). Backend rejects with 429 after limit.
- **Max input length:** 600 characters. Validated before hitting LangChain.
- **Max output tokens:** 450 per LLM response (controlled via OpenRouter params).
- **CORS:** whitelist production domain only; localhost allowed in dev.
- **No secrets in frontend:** all LLM calls go through the backend. Frontend never sees the OpenRouter key.
- **Admin endpoint:** protected by `X-Admin-Key` header (env var), used only for knowledge base ingestion.
- **Input sanitisation:** strip HTML/script tags before passing to LangChain.

### 3.4 LangChain Agent

- Model: `openai/gpt-4o-mini` via OpenRouter (same OpenAI-compatible SDK already in use)
- Agent type: `create_openai_tools_agent` with 3 tools
- System prompt: positions the agent as a pragmatic SMB automation consultant — direct, no fluff, conservative ROI estimates
- Conversation history: passed in full per request (stateless backend — no in-memory sessions)
- Logging: every agent invocation logged with session_id, tokens, cost, tools called

---

## 4. LangChain Tools

### Tool 1 — `calculate_roi`
**Type:** Pure Python function (zero LLM cost, always deterministic)

```python
def calculate_roi(hours_saved_per_week: float, hourly_rate: float, setup_cost: float) -> dict:
    """Calculate automation ROI for a small business task."""
```

Returns: annual savings, payback period in weeks, 3-year net ROI %, recommended decision (automate / borderline / not worth it based on payback > 52 weeks threshold).

Agent calls this when the user mentions time spent on a task and/or asks if automation is worth it.

### Tool 2 — `search_automation_patterns`
**Type:** RAG retrieval over pgvector knowledge base

```python
def search_automation_patterns(task_description: str) -> str:
    """Search the knowledge base for matching automation patterns."""
```

Returns top 2 matching documents (pattern name, problem description, automation approach, example n8n node chain). Uses pgvector cosine similarity. Embeddings: `text-embedding-3-small` via OpenRouter in prod, `nomic-embed-text` via Ollama in dev.

Agent calls this to ground answers in concrete patterns rather than hallucinating workflows.

### Tool 3 — `suggest_tool_stack`
**Type:** Rule-based Python (zero LLM cost, deterministic)

```python
def suggest_tool_stack(monthly_budget: str, tech_comfort: str) -> dict:
    """Recommend an automation tool (n8n, Make, Zapier) based on budget and technical comfort."""
```

Inputs are categorical: `monthly_budget` ∈ {free, under_50, under_200, over_200}; `tech_comfort` ∈ {none, some, developer}. Returns tool name + one-sentence rationale + approximate monthly cost.

Agent calls this when the user asks "what tool should I use?" or when recommending a solution.

---

## 5. Knowledge Base — Hybrid Static + Live Docs

The KB has two layers, both stored as embeddings in pgvector and queried identically by `search_automation_patterns`.

### 5.1 Static Layer (curated by Roman)

6 markdown files for v1 — one per automation use case. These are Roman's IP: opinionated, practical, written from real consulting experience. They don't go stale because they cover patterns, not specific tool versions.

```markdown
# Pattern: [Name]
## Problem
## Manual time cost (benchmark)
## Automation approach
## Recommended tools
## n8n node outline
## ROI benchmark
```

**Covered use cases (v1):**
1. Lead capture from contact forms → CRM/spreadsheet
2. Email parsing → extract data, log to database
3. Invoice/bill generation from spreadsheet rows
4. Appointment booking → calendar + confirmation email
5. New client onboarding (welcome email, folder creation, CRM entry)
6. Social media post scheduling

### 5.2 Live Docs Layer (auto-refreshed)

Key pages from official tool documentation are fetched, chunked, and embedded into pgvector on a schedule (or manually via `/admin/ingest?source=live`). This keeps the agent current as tools evolve — n8n adding nodes, Twenty CRM releasing features, etc.

**Sources (v1):**
| Tool | Pages to index |
|------|---------------|
| n8n | Integrations index, core nodes reference |
| Twenty CRM | Getting started, API reference, automation triggers |
| Make.com | Scenario basics, app connectors |
| Zapier | Zap creation guide, popular integrations |
| Lovable | What it builds, limitations, use cases |

**How it works:**
- `rag/live_ingester.py` fetches each source URL, strips nav/footer, chunks at 800 tokens with 100-token overlap
- Upserted into `documents` table with `source=live` and `fetched_at` metadata
- Re-ingestion scheduled: weekly via a Render cron job (or triggered manually by Roman when a major tool releases an update)
- The agent's `search_automation_patterns` tool queries all documents regardless of source — static and live are indistinguishable to the agent

**This satisfies the Turing "Medium optional: real-time data updates to knowledge base" requirement.**

Files stored in `backend/knowledge_base/`. Static docs ingested at first deploy. Live docs ingested via cron or manual trigger.

---

## 6. Database Schema (Supabase / PostgreSQL + pgvector)

```sql
-- Conversation tracking + rate limiting
CREATE TABLE conversations (
    session_id UUID PRIMARY KEY,
    turn_count INTEGER NOT NULL DEFAULT 0,
    history JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- RAG document store
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(1536),  -- text-embedding-3-small dimensions
    metadata JSONB DEFAULT '{}',
    content_hash TEXT UNIQUE  -- for idempotent re-ingestion
);

CREATE INDEX ON documents USING ivfflat (embedding vector_cosine_ops);
```

---

## 7. Project Structure

```
automate-this/
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app, CORS, middleware
│   │   ├── agent.py          # LangChain agent setup
│   │   ├── config.py         # Pydantic Settings, env vars
│   │   ├── tools/
│   │   │   ├── roi.py        # calculate_roi
│   │   │   ├── search.py     # search_automation_patterns
│   │   │   └── stack.py      # suggest_tool_stack
│   │   ├── rag/
│   │   │   ├── ingest.py     # KB ingestion pipeline
│   │   │   └── retriever.py  # pgvector retriever setup
│   │   └── db.py             # SQLAlchemy async engine + session
│   ├── knowledge_base/       # 15 markdown pattern files
│   ├── pyproject.toml        # uv-managed dependencies
│   ├── Dockerfile
│   └── .env.example
├── frontend/                 # Next.js — design phase (separate Claude session)
│   └── [TBD — awaiting UI design]
├── docker-compose.yml        # Local dev: Postgres + Ollama
├── .env.example
└── README.md
```

---

## 8. Environment Variables

```bash
# LLM
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
CHAT_MODEL=openai/gpt-4o-mini
EMBEDDING_MODEL=openai/text-embedding-3-small

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/automate_this

# Security
ADMIN_API_KEY=             # for /admin/ingest
ALLOWED_ORIGINS=https://yourdomain.com,http://localhost:3000

# Rate limiting
MAX_TURNS_PER_SESSION=8
MAX_INPUT_CHARS=600
MAX_OUTPUT_TOKENS=450

# Local dev overrides (Ollama)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

---

## 9. Frontend (Next.js) — Design Pending

UI will be designed in a separate Claude session focused on visual aesthetics and UX. The frontend is a Next.js app deployed to Vercel.

**Agreed functional requirements (to be handed to design session):**
- Landing page: clear value proposition, "Try the advisor free" CTA
- Chat widget: embedded on landing page, streaming responses
- Turn counter: "3 of 8 free questions used" displayed in chat
- ROI widget: standalone calculator in sidebar (calls `/chat` with pre-filled context)
- Post-limit CTA: "Want a custom automation plan? Book a call" with Calendly link
- Mobile responsive

**API contract the frontend must consume:**
- `POST /chat` — documented in section 3.2
- `GET /health` — for connection status indicator

---

## 10. Turing Sprint 2 Requirement Coverage

| Requirement | Implementation | Location |
|-------------|----------------|----------|
| RAG with embeddings + chunking | pgvector + text-embedding-3-small | `rag/` |
| ≥3 tool calls | calculate_roi, search_kb, suggest_stack | `tools/` |
| LangChain | `create_openai_tools_agent` | `agent.py` |
| OpenRouter | OpenAI-compatible SDK | `config.py` |
| Domain specialisation | SMB automation consulting | system prompt + KB |
| Security measures | rate limiting, input validation, CORS | `main.py` |
| UI | Next.js (allowed: "Python backend + JS frontend") | `frontend/` |
| Error handling | FastAPI exception handlers + LangChain fallbacks | `main.py` |
| Logging | structlog per-request + tool call logging | `main.py` |
| Medium: token cost display | returned in every `/chat` response | response schema |
| Medium: conversation export | export history from session JSONB | future endpoint |

---

## 11. Build Phases and Session Plan

Each Claude session is scoped to a single area of focus. Each session gets its own implementation plan file (created by writing-plans at session start). Manual tasks between sessions are listed explicitly so nothing is blocked.

---

### MANUAL SETUP (before any Claude session)
**You do this yourself — account creation and credentials.**

- [ ] Create new GitHub repo: `automate-this`
- [ ] Create Supabase project (free tier) — save `DATABASE_URL`
- [ ] Create Render.com account — note: backend deploys here
- [ ] Create Vercel account — note: frontend deploys here
- [ ] Get OpenRouter API key (you already have this)
- [ ] Copy `.env.example` → `.env.local`, fill in values

---

### Phase 1 — Turing Submission

**Claude Session 1 — Project scaffold**
_Plan file: `docs/plans/session-1-scaffold.md`_
- FastAPI app skeleton (`main.py`, `config.py`, `db.py`)
- Pydantic Settings wired to env vars
- `/health` endpoint returning JSON
- SQLAlchemy async engine connecting to Supabase
- Docker Compose for local dev (Postgres + Ollama)
- `pyproject.toml` with uv, all dependencies declared
- `.env.example` committed (no secrets)

**You between sessions:** Run `docker compose up`, confirm `/health` returns 200. Create Supabase tables by running `init.sql`.

---

**Claude Session 2 — Knowledge base + ingestion**
_Plan file: `docs/plans/session-2-knowledge-base.md`_
- Write 6 static pattern markdown files in `knowledge_base/`
- `rag/ingest.py`: chunk + embed + upsert to pgvector
- `rag/live_ingester.py`: fetch live docs from n8n, Twenty, Make, Zapier, Lovable
- `rag/retriever.py`: LangChain PGVector retriever
- `/admin/ingest` endpoint (static + live modes)
- Unit test: ingest 1 doc, query it back, confirm similarity > 0.7

**You between sessions:** Call `/admin/ingest` locally, verify documents appear in Supabase table.

---

**Claude Session 3 — Tools**
_Plan file: `docs/plans/session-3-tools.md`_
- `tools/roi.py`: `calculate_roi` with full return schema
- `tools/stack.py`: `suggest_tool_stack` rule table
- `tools/search.py`: `search_automation_patterns` calling retriever
- Unit tests for all three tools (pure Python, no DB needed for roi + stack)
- Integration test for search tool against local Postgres

**You between sessions:** Run tests, confirm all pass.

---

**Claude Session 4 — LangChain agent + /chat endpoint**
_Plan file: `docs/plans/session-4-agent.md`_
- `agent.py`: `create_openai_tools_agent` with system prompt + 3 tools
- `/chat` endpoint: validates input, loads/saves conversation to DB, calls agent, returns structured response
- Conversation history passed per-request (stateless agent)
- Token + cost calculation in response
- Manual test: send 3 messages in sequence, confirm history accumulates

**You between sessions:** Test the chat endpoint with curl/Postman. Ask it a real question about automating lead capture.

---

**Claude Session 5 — Security, rate limiting, logging**
_Plan file: `docs/plans/session-5-security.md`_
- Rate limiting middleware (8 turns/session enforced in DB)
- Input sanitisation (HTML strip, length check)
- CORS configuration (env-driven allowlist)
- structlog per-request logging (session_id, tools_called, tokens, cost, latency)
- 429 response after turn limit with CTA message
- Error handling: LangChain timeout, OpenRouter errors, DB errors — all return clean JSON

**You between sessions:** Try to exceed 8 turns, confirm 429 is returned. Review logs.

---

**Claude Session 6 — Basic Next.js chat UI**
_Plan file: `docs/plans/session-6-frontend-scaffold.md`_
- Next.js app scaffold (TypeScript, App Router)
- Chat page: input box, message list, turn counter
- Calls backend `/chat` API (env var: `NEXT_PUBLIC_API_URL`)
- Session ID generated client-side (UUID, stored in sessionStorage)
- Tool call badges shown under assistant messages ("🔧 calculate_roi used")
- Cost display per message
- No design system yet — functional only (design applied Phase 2)

**You between sessions:** Run frontend locally, have a real conversation end-to-end. This is your Turing demo.

---

**Claude Session 7 — Deployment + Turing submission**
_Plan file: `docs/plans/session-7-deploy.md`_
- Render deployment config (`render.yaml`)
- Vercel deployment config (`vercel.json`)
- Supabase: enable pgvector extension, run `init.sql`
- Environment variables set in Render + Vercel dashboards
- Trigger live doc ingestion on deployed backend
- `README.md`: setup instructions, architecture diagram, Turing requirement coverage table
- Submission document (`docs/submission/sprint-2.md`)

---

### Phase 2 — Production Launch (after UI design session)

**MANUAL TASK:** Run Claude design session using the handover prompt in Section 13. Receive design output (component specs, colour palette, layout).

**Claude Session 8 — Production UI implementation**
_Plan file: `docs/plans/session-8-ui-production.md`_
- Implement landing page to design spec
- Chat widget with streaming responses
- ROI calculator sidebar widget
- Post-limit CTA with Calendly link
- Mobile responsive
- Deploy to Vercel production

**Claude Session 9 — Production hardening** _(optional, post-launch)_
- Analytics integration (Plausible)
- Custom domain + SSL
- Render cron job for weekly live doc refresh
- Monitoring alerts (Render health check + email notification)

---

## 13. UI Design Handover — Claude Design Session Brief

**Use this prompt verbatim when opening a new Claude design session.**

---

> I'm building a production web app called **"Automate This"** — an AI-powered chatbot that helps small business owners figure out what they can automate and how much it'll save them. Think of it as a free 2-minute consultation with an automation expert, available 24/7 on a website.
>
> **Target user:** Non-technical small business owner (30–55 years old). Frustrated with repetitive admin work. Skeptical of tech promises. Responds to concrete numbers and plain language. Does NOT want to feel sold to.
>
> **What the website needs to do (functionally):**
> 1. **Landing page** — convince a skeptic this is worth 2 minutes of their time. Clear headline, one CTA: "Try it free — no signup"
> 2. **Chat widget** — embedded on the landing page below the fold. Shows conversation with an AI consultant. Displays a turn counter ("3 of 8 free questions used"). Shows small tool badges when the AI uses a calculator or searches its knowledge base.
> 3. **ROI sidebar** — a standalone widget alongside the chat showing running numbers: "Estimated annual savings: €X" — updates as the conversation progresses
> 4. **Post-limit CTA** — after 8 turns, the chat locks and shows a message: "Want a full custom automation plan? Book a free 30-min call" with a calendar booking button
>
> **Brand tone:** Confident and direct, like a consultant who's seen it all. Not corporate. Not startup-hyped. Eastern European pragmatism — results over promises. Think less "AI magic ✨" and more "here's your payback period in 6 weeks."
>
> **Technical constraints for the designer to know:**
> - Frontend is Next.js (TypeScript, App Router) deployed to Vercel
> - The chat API returns: `{ reply, tool_calls[], turn_number, tokens_used, cost_usd }`
> - Tool calls to surface in UI: `calculate_roi`, `search_automation_patterns`, `suggest_tool_stack`
> - Must work on mobile (most SMB owners will be on phone)
> - No user accounts — session is anonymous, lives in browser sessionStorage
>
> **What I need from this design session:**
> 1. Visual direction: colour palette, typography, spacing system — 2-3 options to choose from
> 2. Landing page layout: hero section, social proof / trust signals, chat embed placement
> 3. Chat widget component design: message bubbles, tool call display, turn counter, input area
> 4. ROI sidebar widget design
> 5. Post-limit CTA screen design
> 6. Mobile layout for all of the above
>
> **Competitor reference (for what NOT to do — too static, no interactivity):** https://automajestic.co/ai-automatiseerimise-roi-kalkulaator
>
> Please start by proposing 2-3 visual directions (colour + typography + mood) before going into layouts.

---

## 12. Out of Scope (v1)

- User authentication / accounts
- Payment / subscription gating
- Multi-language support
- Fine-tuned models
- A/B testing RAG strategies
- Webhook integrations (n8n calling the API)
