# Automate This — SMB Automation Advisor

A domain-specialised AI chatbot built for Turing College Sprint 2 (Building Applications with LangChain, RAGs, and Tool Calling). Small business owners describe a repetitive manual task; the advisor grounds its answer in a curated + live-refreshed knowledge base, calculates ROI on request, and — once a user has given their name and email — saves them as a qualified lead directly in Twenty CRM. Also the working foundation for a real AI-automation-consulting business.

**GitHub:** https://github.com/RomanMerg/rmerge-AE.2.5

---

## What It Does

1. **Grounded advice** — `POST /chat` runs a session/turn-managed conversation. The LLM decides when to call `search_automation_patterns`, which retrieves the most relevant automation patterns from pgvector (curated knowledge base + live-refreshed tool documentation) rather than hallucinating a workflow.
2. **ROI on demand** — once the user gives hours saved/week, hourly rate, and a rough setup cost, `calculate_roi` (pure, deterministic Python — no LLM cost) returns annual savings, payback period, 3-year net savings, and an automate/borderline/not-worth-it recommendation.
3. **Lead capture** — once the user has explicitly given a name and email, `capture_lead` (a standalone FastMCP tool server) creates a Person record in Twenty CRM and attaches a note with their stated pain point — real REST API calls verified against a live Twenty CRM instance, not mocked.
4. **Rate-limited, stateless-server sessions** — 8 turns per `session_id` AND 30 requests per IP per hour (independent of `session_id`, closing the session-cycling bypass); conversation history now lives in LangGraph's Postgres checkpointer (thread_id = session_id), 429 once either limit is hit.
5. **Streaming replies** — `POST /chat/stream` runs the same LangGraph agent but streams the reply as Server-Sent Events (`token` events as the LLM generates, then one `done` event with the full response payload) — the transport the Next.js frontend will consume.
6. **Demo UI** — a Gradio chat interface for manual testing and the Sprint 2 submission demo (the planned production frontend is Next.js, built separately once UI design work happens — see [Roadmap](#roadmap--known-gaps)).

---

## Stack

| Component | Choice | Rationale |
|---|---|---|
| API | FastAPI (async) | `/chat`, `/chat/stream`, `/health`, `/admin/ingest` |
| LLM orchestration | LangGraph (explicit `StateGraph` agent) + `langchain-openai` `ChatOpenAI` | Multi-step tool loop with a recursion limit (10), not a fixed 2-call loop or `AgentExecutor` |
| LLM | `openai/gpt-4o-mini` via OpenRouter | Mandatory OpenAI-compatible SDK requirement |
| Embeddings | `text-embedding-3-small` via OpenRouter (1536-dim) | Matches the `VECTOR(1536)` schema |
| Vector store | pgvector (PostgreSQL 16, ivfflat index) | Cosine similarity search over curated + live docs |
| Tool protocol | FastMCP | `capture_lead` is a real MCP tool server, callable standalone (`fastmcp run`) or imported in-process by `/chat` |
| CRM | Twenty CRM (self-hosted, REST API) | Lead storage — Person + linked Note per capture |
| ORM | SQLAlchemy async + asyncpg, raw `text()` SQL | No ORM models — explicit SQL throughout |
| Observability | Langfuse (env-gated) + structlog JSON logs | `chat_turn` structured log per request; Langfuse traces when keys are set, clean no-op otherwise |
| Memory | LangGraph `AsyncPostgresSaver` checkpoints (thread_id = session_id) | Conversation history lives in LangGraph's own Postgres tables, not `conversations.history` |
| Demo UI | Gradio | Fast, disposable Sprint 2 demo harness |
| Package manager | uv | No `pip install` anywhere in the project |
| Tests | pytest-asyncio | 110 unit + 3 integration (113 total) |

---

## Tools (LangChain `bind_tools`)

| Tool | Type | What it does |
|---|---|---|
| `search_automation_patterns` | RAG retrieval (async) | Embeds the query, pgvector cosine search (top-3) over static + live KB, returns formatted context for the LLM plus `{title, similarity}` sources for the API response |
| `calculate_roi` | Pure Python, deterministic, zero LLM cost | `hours_saved_per_week × hourly_rate` → annual savings, payback weeks, 3-year net savings, `automate` / `borderline` / `not worth it` |
| `capture_lead` | FastMCP tool → Twenty CRM REST API | Creates a Person (`POST /rest/people`), a Note with the stated pain point (`POST /rest/notes`, `bodyV2.markdown`), and links them (`POST /rest/noteTargets`) — only invoked by the LLM once name AND email have been explicitly given |

All three are bound to a single `ChatOpenAI` client as raw `{"type": "function", ...}` schemas (not `@tool`-decorated functions) inside one bounded 2-call loop per turn: first call may return tool calls, they execute, results feed back as `ToolMessage`s, second call produces the reply.

---

## Sprint 2 Requirement Coverage — part of learning project submission

### Core Requirements

| Requirement | Status | Implementation |
|---|---|---|
| RAG with embeddings + chunking + similarity search | ✅ | `text-embedding-3-small`, pgvector ivfflat cosine search; live docs chunked 800 tok / 100 overlap |
| ≥3 different tool calls | ✅ | `search_automation_patterns`, `calculate_roi`, `capture_lead` |
| LangChain with OpenRouter | ✅ | `langchain_openai.ChatOpenAI` bound to 3 tools, OpenRouter as the OpenAI-compatible endpoint |
| Domain specialisation, focused KB | ✅ | SMB automation consulting; 6 curated pattern files + live n8n/Twenty/Make/Zapier/Lovable docs |
| Proper error handling | ✅ | Empty-LLM-reply fallback, `capture_lead` never raises (isolates person-creation success from note/link failure), `AsyncSessionLocal` scoped per request |
| Input validation, rate limiting, API key management | ✅ | `max_input_chars` → 422, `max_turns_per_session` → 429, `X-Admin-Key` on `/admin/ingest`, all secrets via `.env` / `get_settings()` (never `os.getenv` bypasses) |
| Logging and monitoring | ✅ | Structured `chat_turn` JSON log per request (structlog: session_id, tools called, tokens, cost, latency) + Langfuse LLM traces (env-gated) |
| UI — Streamlit/Next.js | ⚠️ Substituted | Built with **Gradio** (sprint doc's own accepted "Python Track: Alternative") as a fast, disposable demo harness. Production Next.js frontend is a deliberately separate, later build — see [Roadmap](#roadmap--known-gaps) |
| Show context/sources, display tool results | ✅ | Gradio renders source citations with similarity scores per reply |
| Progress indicators for long operations | ⬜ Not done | |

### Optional Tasks

| Task | Tier | Status |
|---|---|---|
| Include source citations in responses | Easy | ✅ `sources: [{title, similarity}]` on every `/chat` response where search fired |
| Add real-time data updates to knowledge base | Medium | ✅ `live_ingester.py` fetches/chunks/embeds n8n, Twenty CRM, Make, Zapier, Lovable docs; `POST /admin/ingest?source=live` |
| Calculate and display token usage and costs | Medium | ✅ `tokens_used`/`cost_usd` on every `/chat` response; real chat-completion token counts via LangChain's `usage_metadata`, estimated embedding tokens |
| **Implement your tools as MCP servers** | Hard | ✅ `capture_lead` is a real FastMCP server (`uv run fastmcp run mcp_server/server.py`), not just an in-process function |
| Conversation history/export, RAG viz, chatbot guide | Easy | ⬜ History persists to DB but no export endpoint; no RAG-process visualisation |
| Multi-model support, caching, auth/personalisation, tool-result viz, conversation export, remote MCP server | Medium | ⬜ Not done |
| Cloud deploy w/ scaling, advanced indexing (RAPTOR/ColBERT), A/B testing, scheduled automated KB refresh, fine-tuning, multi-language, analytics dashboard, RAGAs evaluation | Hard | ⬜ Not done — local dev only, `live_ingester` is manually triggered, ivfflat is the only index strategy |

**Current count toward "2 Medium + 1 Hard" max-points target: 2 Medium + 1 Hard.**

---

## Security Measures

- **Rate limiting** — 8 turns per `session_id`, enforced in Postgres (`conversations.turn_count`), not just client-side; `429` once exceeded.
- **Input validation** — message length capped (`max_input_chars`, default 600), `422` on violation, checked before any LLM/embedding call.
- **Admin endpoint auth** — `/admin/ingest` requires `X-Admin-Key`, compared against `ADMIN_API_KEY` from settings.
- **CORS allowlist** — `ALLOWED_ORIGINS` env-driven, not wildcard.
- **Per-IP rate limiting** — `chat_requests` table logs every `/chat` call by IP; more than `max_requests_per_ip_per_hour` (default 30) in a rolling hour returns `429`, independent of `session_id` (closes the session-cycling bypass — a script can't dodge the per-session turn limit just by generating a fresh `session_id` every call).
- **No secrets in the frontend** — Gradio (and the future Next.js UI) only ever calls `POST /chat`; OpenRouter/Twenty CRM keys never leave the backend.
- **Settings discipline** — every credential is read via `get_settings()` (pydantic-settings, cached singleton); no module bypasses this with raw `os.getenv()` (this was a real bug, found and fixed — see [Roadmap](#roadmap--known-gaps)).
- **Tool-call guardrail is prompt-level, not a hard validator** — `capture_lead`'s tool description instructs the LLM to only call it once name AND email are explicit; there is no separate input/intent classifier guard (a gap relative to a stricter security posture — noted honestly, not hidden).

---

## Database Schema

PostgreSQL 16 + `pgvector`. Two tables, both created by `init.sql` on first container start.

```sql
-- Conversation tracking and per-session rate limiting
CREATE TABLE conversations (
    session_id  UUID        PRIMARY KEY,
    turn_count  INTEGER     NOT NULL DEFAULT 0,
    history     JSONB       NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RAG document store — static patterns + live docs share this table
CREATE TABLE documents (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title        TEXT        NOT NULL,
    content      TEXT        NOT NULL,
    embedding    VECTOR(1536),
    metadata     JSONB       NOT NULL DEFAULT '{}',
    content_hash TEXT        UNIQUE,   -- sha256, idempotent re-ingest
    UNIQUE (title)
);

-- Per-IP request log, backs the hourly rate limit (independent of session_id)
CREATE TABLE chat_requests (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ip_address TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

`documents_embedding_idx` is an ivfflat index (`lists = 10`, sized for a small KB — raise as it grows past ~10k rows). Search always sets `ivfflat.probes = 10` locally for full recall on a small dataset. `chat_requests` has an index on `(ip_address, created_at)`; rows older than 24h are opportunistically pruned on each insert, so the table doesn't grow unbounded.

> **Existing running Postgres containers** (e.g. from an earlier session) won't pick up `chat_requests` automatically — `init.sql` only runs on first container creation. Apply it manually against a live DB: `CREATE TABLE IF NOT EXISTS chat_requests (...)` from `init.sql`, then the matching index.

**`conversations.history` is no longer written.** Conversation history now lives in LangGraph's own checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes` — plus `checkpoint_migrations`), auto-created by `AsyncPostgresSaver.setup()` on every app startup (idempotent). `conversations` is kept only for `turn_count` (the per-session rate limit) and for backward compatibility with the `history` column shape; nothing reads it anymore. Sessions created before this change keep their old JSONB history in `conversations.history`, but that history is **not** migrated into a LangGraph checkpoint — the first post-migration turn in an old session starts with no memory of what came before. This is a dev-only environment with no real users yet, so the gap is accepted rather than fixed with a migration script.

---

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | None | Uptime check |
| POST | `/admin/ingest` | `X-Admin-Key` | Ingest KB into pgvector — `?source=static` (6 curated files) or `?source=live` (n8n/Twenty/Make/Zapier/Lovable docs) |
| POST | `/chat` | None | Session-managed, tool-calling chat via the LangGraph agent. Request: `{session_id?, message}`. Response: `{session_id, reply, sources, turns_remaining, tokens_used, cost_usd}`. `429` on either the per-session turn limit or the per-IP hourly rate limit, distinguished by the `detail` message |
| POST | `/chat/stream` | None | Same request body as `/chat`. Rejection paths (422 message-too-long, 429 rate limits) fire as plain HTTP errors **before** the stream opens. Once open, response is `text/event-stream` SSE: zero or more `event: token` frames (`data: {"content": "..."}` — one per LLM text delta from the agent node only, not tool-call argument deltas), then exactly one terminal frame — either `event: done` (`data:` = the same JSON shape as `/chat`'s response body) or, if the turn fails mid-stream, `event: error` (`data: {"detail": "..."}`) |

---

## Knowledge Base

Six curated automation patterns in `backend/knowledge_base/`, each with manual time-cost benchmarks, recommended tools, an n8n node outline, and an ROI benchmark:

- **Lead Capture** — CRM auto-population from web forms
- **Email Parsing** — extract structured data from inbound emails
- **Invoice Generation** — auto-generate invoices from completed jobs
- **Appointment Booking** — calendar sync and confirmation flows
- **Client Onboarding** — welcome sequences and account provisioning
- **Social Media Scheduling** — content queue and multi-platform posting

Supplemented by live-fetched documentation (n8n, Twenty CRM, Make.com, Zapier, Lovable) — same table, `metadata.source = "live"`, indistinguishable to `search_automation_patterns` beyond the metadata tag.

---

## Observability

**Structured logs** — every `/chat` and `/chat/stream` call emits one `chat_turn` JSON log line via `structlog` (configured in `app/logging_config.py`), regardless of whether Langfuse is configured:

```json
{"session_id": "...", "tools_called": ["search_automation_patterns", "calculate_roi"], "tokens_used": 3378, "cost_usd": 0.000712, "latency_ms": 12963, "turns_remaining": 7, "event": "chat_turn", "level": "info", "timestamp": "2026-07-03T11:55:16.015003Z"}
```

`/chat/stream`'s version of this line adds `"streamed": true`. A `startup_complete` line is also logged once, on app boot, after the LangGraph checkpointer's tables are created.

**Langfuse tracing** — optional and env-gated (`app/observability.py`). Leave `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` blank in `.env` and tracing is a clean no-op (`get_langfuse_callbacks()` returns `[]`, no warnings, no errors). To enable:

1. Create a free project at [cloud.langfuse.com](https://cloud.langfuse.com) → Settings → API Keys.
2. Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and (if self-hosting Langfuse) `LANGFUSE_HOST` in `backend/.env`.
3. Restart the server. Every graph invocation now runs with a `langfuse.langchain.CallbackHandler` in its LangChain callbacks, and `metadata.langfuse_session_id` is set to the chat `session_id`, so traces are filterable per conversation in the Langfuse dashboard.

---

## Infrastructure

| Service | Host | Port |
|---|---|---|
| FastAPI backend | localhost | 8000 (or next free port — Twenty CRM and other local services may already occupy 8000) |
| Gradio demo UI | localhost | 7860 |
| PostgreSQL + pgvector | localhost | 5432 |
| Twenty CRM | localhost | 3001 |
| OpenRouter API | openrouter.ai | 443 |

Postgres runs via `docker-compose.yml`; Twenty CRM runs as a separately-managed local instance (not part of this repo's compose stack).

---

## Running Locally

**Prerequisites:** Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker (for Postgres) or an existing Postgres 16 + pgvector instance, an OpenRouter API key, and a running Twenty CRM instance with an API key (Settings → API → Generate new key) if you want `capture_lead` to work end-to-end.

```bash
# Clone and install
git clone https://github.com/RomanMerg/rmerge-AE.2.5.git
cd rmerge-AE.2.5/backend
uv sync

# Configure environment
cp .env.example .env
# Fill in OPENROUTER_API_KEY, DATABASE_URL, ADMIN_API_KEY, TWENTY_API_KEY, TWENTY_BASE_URL

# Start Postgres (from repo root)
cd ..
docker compose up -d

# Ingest the knowledge base
curl -X POST "http://localhost:8000/admin/ingest?source=static" -H "X-Admin-Key: your_admin_key"

# Run the API
cd backend
uv run python run_server.py 8000
# note: on Windows, plain uvicorn (`uv run python -m uvicorn app.main:app --port 8000`)
# hangs at startup — its default ProactorEventLoop is rejected by psycopg's async pool
# (used for the LangGraph Postgres checkpointer in app/main.py's lifespan). run_server.py
# pins the selector event loop policy on win32 and drives uvicorn programmatically; on
# Linux/macOS it behaves like plain uvicorn. Hot-reload is not supported by this launcher
# on Windows (restart manually after code changes; Linux/macOS can still use plain
# `uv run python -m uvicorn app.main:app --reload --port 8000`). A DeprecationWarning
# about WindowsSelectorEventLoopPolicy at startup is expected and harmless.

# Run the Gradio demo (separate terminal)
uv run python frontend/app.py
# If port 8000 is already taken locally, run the backend on another port and point
# the demo at it: BACKEND_API_URL=http://localhost:8001 uv run python frontend/app.py
```

API at `http://localhost:8000` (docs at `/docs`), Gradio demo at `http://localhost:7860`.

```bash
# Try it
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I spend 4 hours a week manually creating invoices in Word and emailing them"}'
```

### Testing the new limits from the Gradio UI

**Token/cost display** — just chat normally. Every reply now ends with a line like `*7 turns remaining · 150 tokens · $0.000123 this turn.*` — no setup needed beyond having the server and UI running (see above).

**Per-session turn limit (8 turns)** — send 8 messages in the same browser session (don't click "New conversation" between them). The 9th returns `429` with detail `"Session turn limit reached"`, shown in the chat as an error message.

**Per-IP rate limit (30/hour)** — harder to trigger by hand in real time, since it's independent of `session_id`: clicking "New conversation" resets the turn counter but not the IP counter, so you'd need 31 real `/chat` calls (across any number of sessions) within an hour to see it fire naturally. The fast way to actually see the `429` while testing:

1. Temporarily lower the limit for a quick test: add `MAX_REQUESTS_PER_IP_PER_HOUR=3` to `backend/.env`.
2. Restart the server (`Ctrl+C`, re-run `uv run python run_server.py 8000`) — settings are cached per-process, so a running server won't pick up the change.
3. Send 4 messages via the Gradio UI (clicking "New conversation" in between is fine — this limit doesn't care about `session_id`). The 4th should return `429` with detail `"Too many requests from this IP — try again later"`.
4. Remove the override (or set it back to `30`) and restart the server again before real use.

---

## Running Tests

```bash
cd backend

# Unit tests only (no DB required) — 110 tests
uv run python -m pytest -m "not integration"

# Integration tests (requires live Postgres with pgvector) — 3 tests
uv run python -m pytest -m integration -v
```

> `uv run pytest` (without `python -m`) is broken in some checkouts — always use `uv run python -m pytest`.

---

## Project Structure

```
rmerge-AE.2.5/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app: /health, /admin/ingest, /chat, /chat/stream (LangGraph agent)
│   │   ├── agent.py             # build_agent_graph() — explicit StateGraph tool loop, per-turn state reset
│   │   ├── config.py            # Pydantic Settings — every credential goes through get_settings()
│   │   ├── db.py                # Async SQLAlchemy engine + session factory
│   │   ├── logging_config.py    # structlog JSON configuration
│   │   ├── observability.py     # get_langfuse_callbacks() — env-gated Langfuse tracing
│   │   ├── rate_limit.py        # get_client_ip(), check_ip_rate_limit() — per-IP hourly cap, Postgres-backed
│   │   ├── cost_tracker.py      # calculate_cost(), estimate_embedding_tokens(), MODEL_PRICING
│   │   ├── rag/
│   │   │   ├── ingest.py        # Static KB ingestion — sha256 idempotency, embed_text(), pgvector upsert
│   │   │   ├── retriever.py     # search_documents() — cosine similarity, ivfflat probes=10
│   │   │   └── live_ingester.py # Fetch + chunk (800/100 overlap) + embed live docs, 5 sources
│   │   └── tools/
│   │       ├── roi.py           # calculate_roi() — pure, deterministic
│   │       └── search.py        # search_automation_patterns() — wraps rag/ for LLM tool-calling
│   ├── mcp_server/
│   │   └── server.py            # FastMCP capture_lead — real Twenty CRM /rest/ API calls
│   ├── knowledge_base/          # 6 curated automation pattern markdown files
│   └── tests/                   # 110 unit + 3 integration (pytest-asyncio, asyncio_mode=auto)
├── frontend/
│   └── app.py                   # Gradio chat demo — disposable Sprint 2 harness
├── docs/
│   ├── superpowers/
│   │   ├── specs/                                    # Original approved design spec
│   │   └── plans/2026-07-02-session-3-tools-langchain.md  # Executed plan for tools + LangChain retrofit
│   └── session-3-chat-mcp.md    # Earlier plan — Tasks 1-2 built as-is, Tasks 3-5 superseded (see banner in file)
├── init.sql                     # conversations + documents tables, ivfflat index
└── docker-compose.yml           # Postgres 16 + pgvector for local dev
```

---

## Roadmap / Known Gaps

Documented honestly rather than glossed over:

- **Sprint 3 (next up):** long-term memory (beyond per-thread checkpoints — e.g. user-level facts persisted across sessions), a `suggest_tool_stack` tool, and the Next.js frontend, per a separate Phase 2 plan.
- **Production frontend (Next.js)** — deliberately deferred to its own design + build session; Gradio is explicitly a throwaway demo, not competing with this plan. `/chat/stream` (SSE) exists specifically as the transport it will consume.
- **2 Medium + 1 Hard optional tasks done** — real-time KB updates, MCP-server tools, and token/cost tracking. Still short of the stretch goal of adding more (e.g. conversation export, multi-model support) if pursued further.
- **`live_ingester.py` is manually triggered**, not on a schedule — "automated KB updates" (Hard optional) isn't fully satisfied.
- **No intent/jailbreak guard** on chat input beyond length validation and the LLM's own tool-call judgment — a stricter security posture would add a dedicated input classifier, as this project's Sprint 1 predecessor did.
- **Two real integration bugs were found via live manual testing (not mocks)** during this session and fixed: `capture_lead` was hitting the wrong Twenty CRM REST paths/payload shapes, and separately never actually loaded its API key due to a `os.getenv()` vs `get_settings()` mismatch. Both fixed and re-verified against a real Twenty CRM instance — flagged here as a reminder that mocked test suites alone don't catch integration-boundary bugs.
