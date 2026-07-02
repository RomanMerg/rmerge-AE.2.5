# Session 2 Handover — Knowledge Base + Ingestion Pipeline

**Project:** "Automate This" — SMB Automation Advisor chatbot (Turing College Sprint 2 capstone + AI-automation-consulting business prototype)
**Repo:** `C:\Users\markm\Documents\Claude\Projects\Turing\rmerge-AE` — pushed to https://github.com/RomanMerg/rmerge-AE (remote `origin`, branch `main`)
**Design spec (single source of truth):** `docs/superpowers/specs/2026-06-05-automate-this-design.md`
**Session 1 plan (reference for plan structure/format):** `docs/superpowers/plans/2026-06-05-session-1-scaffold.md`

> Read the design spec sections referenced below before writing the Session 2 plan. Don't take this handover's summaries as a substitute — verify against the spec, it is canonical.

---

## 1. Where things stand

Session 1 ("Project scaffold") is **complete and merged** — 17 commits on `main`, HEAD `1af8272`. All 13 tests pass. Docker stack runs cleanly (Postgres + pgvector only).

**What exists right now:**
```
rmerge-AE/
├── backend/
│   ├── app/
│   │   ├── main.py        # FastAPI app "Automate This API" v0.1.0, CORS, /health (tag "meta")
│   │   ├── config.py      # Pydantic Settings + lazy get_settings() (lru_cache singleton)
│   │   └── db.py          # async engine, AsyncSessionLocal, Base, get_db()
│   ├── tests/
│   │   ├── conftest.py    # env setup via os.environ.setdefault() BEFORE app import; async_client fixture
│   │   └── ...            # 13 passing tests (config, db, health)
│   ├── pyproject.toml     # uv-managed deps
│   ├── Dockerfile         # pinned uv:0.11.19, non-root appuser
│   ├── .dockerignore
│   ├── .env               # gitignored — local dev values already filled in
│   └── .env.example
├── docker-compose.yml     # ONLY postgres service (pgvector/pgvector:pg16) — see §4 Docker warning
├── init.sql               # conversations + documents tables, ivfflat index, updated_at trigger
├── docs/superpowers/
│   ├── specs/2026-06-05-automate-this-design.md
│   ├── plans/2026-06-05-session-1-scaffold.md
│   └── handover/2026-06-08-session-2-handover.md   (this file)
└── knowledge_base/.gitkeep   # empty dir, tracked via .gitkeep — THIS IS WHERE SESSION 2's MD FILES GO
```

**Established code patterns — follow these exactly, do not deviate:**

- **Settings access:** There is NO module-level `settings = Settings()`. Always call `get_settings()` (lazy `@lru_cache` singleton in `app/config.py`). Importing `app.config` must never raise — instantiation is deferred until first call.
- **DB session pattern:** `from app.db import get_db, AsyncSessionLocal, Base, engine`. Use `async with AsyncSessionLocal() as session:` or FastAPI `Depends(get_db)`.
- **Test pattern:** `pytest` + `httpx.AsyncClient` + `ASGITransport`, `asyncio_mode = "auto"`. `conftest.py` sets env vars via `os.environ.setdefault()` **before** importing the app — follow this for any new test modules that need DB/settings.
- **Package manager:** `uv`, not `pip`. Add deps via `uv add <pkg>` from `backend/`.

---

## 2. Session 2 scope (from spec §11, "Claude Session 2 — Knowledge base + ingestion")

Plan file to create: `docs/superpowers/plans/2026-06-0X-session-2-knowledge-base.md` (follow the structure/format of the Session 1 plan file — TDD steps, exact code blocks, file maps, commit messages per task).

**Deliverables:**
1. Write **6 static pattern markdown files** in `backend/knowledge_base/` (replacing `.gitkeep` — keep or remove it per convention, your call, but the dir must end up populated)
2. `backend/app/rag/ingest.py` — chunk + embed + upsert to pgvector (idempotent via `content_hash` sha256 — see schema)
3. `backend/app/rag/live_ingester.py` — fetch live docs from n8n, Twenty CRM, Make.com, Zapier, Lovable
4. `backend/app/rag/retriever.py` — LangChain PGVector retriever setup
5. `/admin/ingest` endpoint (static + live modes, admin-key protected — uses `get_settings().admin_api_key`)
6. Unit test: ingest 1 doc, query it back, confirm cosine similarity > 0.7

**End-of-session manual task for Roman:** Call `/admin/ingest` locally, verify documents appear in the Supabase `documents` table.

### 2.1 The 6 static KB markdown files — exact spec (design spec §5.1)

Each file follows this template exactly:
```markdown
# Pattern: [Name]
## Problem
## Manual time cost (benchmark)
## Automation approach
## Recommended tools
## n8n node outline
## ROI benchmark
```

These are Roman's consulting IP — opinionated, practical, written from real-world experience. They cover *patterns*, not specific tool versions, so they don't go stale. **The implementer subagent should draft realistic, useful content for each section** (not placeholder text) — these will be embedded and retrieved by the live chatbot, so quality matters for the Turing demo and for the RAG quality test.

**The 6 use cases (must cover exactly these, one file each):**
1. Lead capture from contact forms → CRM/spreadsheet
2. Email parsing → extract data, log to database
3. Invoice/bill generation from spreadsheet rows
4. Appointment booking → calendar + confirmation email
5. New client onboarding (welcome email, folder creation, CRM entry)
6. Social media post scheduling

### 2.2 Live docs layer (spec §5.2)

| Tool | Pages to index |
|------|---------------|
| n8n | Integrations index, core nodes reference |
| Twenty CRM | Getting started, API reference, automation triggers |
| Make.com | Scenario basics, app connectors |
| Zapier | Zap creation guide, popular integrations |
| Lovable | What it builds, limitations, use cases |

**How `live_ingester.py` should work (per spec):**
- Fetches each source URL, strips nav/footer, chunks at **800 tokens with 100-token overlap**
- Upserts into `documents` table with `metadata.source = "live"` and `metadata.fetched_at` timestamp
- Designed to be re-run on a schedule (Render cron — that's a later session) or manually via `/admin/ingest?source=live`
- The agent's `search_automation_patterns` tool (built in Session 3) queries `documents` regardless of source — static and live docs are indistinguishable to the agent
- This satisfies the Turing "Medium optional: real-time data updates to knowledge base" requirement

### 2.3 Database schema — `documents` table (already created by `init.sql`, Session 1)

```sql
CREATE TABLE documents (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title        TEXT        NOT NULL,
    content      TEXT        NOT NULL,
    embedding    VECTOR(1536),                  -- text-embedding-3-small dimensions
    metadata     JSONB       NOT NULL DEFAULT '{}',
    content_hash TEXT        UNIQUE             -- sha256, for idempotent upsert/re-ingestion
);

CREATE INDEX documents_embedding_idx
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);
```
- **Idempotency:** `content_hash = sha256(content)`. Ingestion should be an upsert keyed on `content_hash` — re-running ingestion on unchanged content must not create duplicates.
- Embedding dimension is locked at **1536** (text-embedding-3-small). If local dev uses Ollama (`nomic-embed-text`) for embeddings, dimensions may differ — check this carefully; the spec's env vars include both `EMBEDDING_MODEL` (OpenRouter/OpenAI, prod) and `OLLAMA_EMBEDDING_MODEL` (local dev fallback). Decide and document which path `ingest.py` uses for local dev vs prod, and whether the schema's `VECTOR(1536)` constrains that choice (it does — Ollama's `nomic-embed-text` is 768-dim, which would NOT fit `VECTOR(1536)` as-is). **Flag this to the user/spec-reviewer if it's ambiguous — it's a real architectural decision, not a guess to make silently.**

### 2.4 Project structure additions (spec §7)

```
backend/app/rag/
├── ingest.py       # KB ingestion pipeline (static docs)
├── live_ingester.py # live docs fetch+chunk+embed (NEW — added in this session, not in original §7 tree but explicitly scoped in §11)
└── retriever.py    # pgvector retriever setup
```

### 2.5 `/admin/ingest` endpoint (spec §3.1 area — verify exact contract against spec §3)

- Protected by `ADMIN_API_KEY` (already in `Settings` as `admin_api_key`, already in `.env`/`.env.example`)
- Should support both static and live ingestion modes (e.g. `?source=static|live` query param, per spec §5.2: "or manually via `/admin/ingest?source=live`")
- Should be tagged appropriately in OpenAPI (follow the `/health` → tag "meta" convention; something like tag "admin")

---

## 3. Environment variables (already configured — verify, don't recreate)

`backend/.env` (gitignored, exists locally) and `backend/.env.example` (committed) already contain:
```bash
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
CHAT_MODEL=openai/gpt-4o-mini
EMBEDDING_MODEL=openai/text-embedding-3-small
DATABASE_URL=postgresql+asyncpg://automate:automate@localhost:5432/automate_this
ADMIN_API_KEY=
ALLOWED_ORIGINS=http://localhost:3000
MAX_TURNS_PER_SESSION=8
MAX_INPUT_CHARS=600
MAX_OUTPUT_TOKENS=450
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```
All of these are already modeled as fields on `Settings` in `app/config.py`. No new env vars should be needed for Session 2 unless the live-ingester needs source URLs — if so, prefer hardcoding the URL list in `live_ingester.py` (per spec table in §2.2) over adding env vars, unless there's a good reason to make them configurable.

---

## 4. ⚠️ CRITICAL — Docker pre-flight checklist (lesson learned the hard way in Session 1)

**Before running ANY `docker compose` or `docker run` command, ALWAYS run `docker ps -a` first** and confirm with the user what's already running. 

**Why this matters:** During Session 1, a subagent ran `docker compose up -d` blind and caused real damage:
- Roman **already runs a persistent `ollama` container** on `localhost:11434` (host-level, not part of this project)
- Roman **already runs Postgres instances** (a Supabase stack) — separate from this project's `rmerge-ae-postgres-1`
- The blind `docker compose up -d` spun up a duplicate `rmerge-ae-ollama-1` that uselessly failed to bind port 11434, created an orphaned volume `rmerge-ae_ollama_data`, and a smoke-test container `elastic_chandrasekhar` was left running on port 8003 for 30+ minutes
- Cleanup required investigating `docker ps -a`, identifying exactly what was spurious vs needed, and removing ONLY the resources created by the rogue run (containers + the orphaned volume) while preserving the project's legitimate `rmerge-ae-postgres-1` container and `postgres_data` volume

**Current state (verified clean as of end of Session 1):** `docker-compose.yml` contains ONLY the `postgres` service. The `ollama` service block was permanently removed with an explanatory comment: `# Ollama intentionally omitted — use the existing host ollama container at localhost:11434`.

**Rule for any subagent touching Docker in Session 2 (there shouldn't be much need, but if `ingest.py` testing requires the stack running):**
1. Run `docker ps -a` first, report what's running, and get confirmation before starting/stopping/creating anything
2. Never add an `ollama` service back to `docker-compose.yml` — Ollama is a host-level resource Roman manages independently
3. Only start the `postgres` service that's already defined; don't `docker compose up -d --build` blindly if containers may already be up
4. Clean up any test/smoke-test containers immediately after use — don't leave them running

---

## 5. Recommended workflow — Subagent-Driven Development

This is how Session 1 was executed successfully (13/13 tests passing, clean reviews). Repeat the pattern:

**Per task:**
1. **Implementer subagent** (model: `haiku` — cost-efficient for well-scoped, well-specified work). Give it the FULL task text from the plan file, scene-setting context, and the established patterns from §1 above. It implements, tests (TDD), commits, self-reviews, and reports.
2. **Spec compliance reviewer** (model: `sonnet`). Verifies the implementer built *exactly* what was asked — nothing missing, nothing extra, no misunderstandings. Reads actual code, doesn't trust the report.
3. **Code quality reviewer** (model: `sonnet`). Verifies clean/tested/maintainable code, single-responsibility files, follows project structure from the plan.
4. **Fix-and-re-review loop** if either reviewer finds issues — re-dispatch implementer with specific findings, then re-review.

**Context window management:** Each subagent starts cold — brief it like a colleague who just walked in. Paste the full task text from the plan (don't make it read the file). Reference established patterns explicitly (the code snippets in §1 of this doc). This keeps each subagent's context tight and the controller's context from bloating with re-derived details.

**Commit discipline:** One commit per task, with a clear message. Follow the message style of existing commits (`git log` to check — e.g. `d9d3ba9`, `8b247f6`, `3fe5797`, `9206125`, `755acc3`, `1af8272`).

---

## 6. Open questions to resolve early in Session 2 (don't guess — ask or flag to reviewer)

1. **Embedding model for local dev vs prod:** `text-embedding-3-small` (1536-dim, via OpenRouter/OpenAI) is what the `documents.embedding VECTOR(1536)` column is sized for. But `Settings` also has `ollama_embedding_model = "nomic-embed-text"` (768-dim) for local dev. Decide: does `ingest.py` always use OpenRouter embeddings (even locally, incurring small API costs), or does it need a dimension-aware path? **This affects the schema and the ingestion code — resolve before writing `ingest.py`.**
2. **Chunking strategy for static KB files:** spec doesn't explicitly state chunk size for the 6 static markdown files (only specifies 800 tokens / 100 overlap for *live* docs). Decide whether static files are embedded whole (they're short, structured docs) or chunked the same way — and document the decision in the plan.
3. **LangChain version/integration for PGVector retriever:** confirm which LangChain pgvector integration package to add via `uv add` (e.g. `langchain-postgres` vs `langchain-community` PGVector) — check current LangChain ecosystem recommendations, as this changes frequently.

---

## 7. Quick reference — full code patterns from Session 1

**`app/config.py`:**
```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    chat_model: str = "openai/gpt-4o-mini"
    embedding_model: str = "openai/text-embedding-3-small"
    database_url: str
    admin_api_key: str
    allowed_origins: str = "http://localhost:3000"
    max_turns_per_session: int = 8
    max_input_chars: int = 600
    max_output_tokens: int = 450
    ollama_base_url: str = "http://localhost:11434"
    ollama_embedding_model: str = "nomic-embed-text"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

**`app/db.py`:**
```python
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

engine = create_async_engine(get_settings().database_url, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models in this project."""

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
```

**`docker-compose.yml`** (current, verified — postgres only):
```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: automate
      POSTGRES_PASSWORD: automate
      POSTGRES_DB: automate_this
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U automate -d automate_this"]
      interval: 5s
      timeout: 5s
      retries: 5

  # Ollama intentionally omitted — use the existing host ollama container at localhost:11434

volumes:
  postgres_data:
```

---

## 8. First steps for the new session

1. Read `docs/superpowers/specs/2026-06-05-automate-this-design.md` sections 5, 6, 7, 8, 11 in full (don't rely solely on this handover's excerpts)
2. Read `docs/superpowers/plans/2026-06-05-session-1-scaffold.md` as a structural reference for how to write the Session 2 plan
3. Run `git log --oneline -20` and `git status` to confirm repo state matches §1 above
4. Resolve the open questions in §6 (ask the user if genuinely ambiguous — these are architectural decisions)
5. Write `docs/superpowers/plans/2026-06-0X-session-2-knowledge-base.md` using the writing-plans skill / TDD task breakdown
6. Execute via Subagent-Driven Development (§5) — one task at a time, implementer → spec review → quality review → commit
7. **Before any Docker command:** run `docker ps -a` and confirm with the user (§4)
