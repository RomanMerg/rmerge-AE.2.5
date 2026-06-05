# Session 1 — Backend Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a production-ready FastAPI backend skeleton with config, DB connection, /health endpoint, and local dev Docker Compose — the foundation every subsequent session builds on.

**Architecture:** Pydantic Settings drives all configuration from environment variables (no hardcoded values anywhere). FastAPI app with CORS middleware. SQLAlchemy async engine ready to connect to pgvector Postgres. All unit tests run without a live database.

**Tech Stack:** Python 3.12, FastAPI 0.115+, SQLAlchemy 2.0 async, asyncpg, Pydantic v2 Settings, uv, pytest + httpx, Docker Compose with `pgvector/pgvector:pg16` + `ollama/ollama`

---

## File Map

| File | Responsibility |
|------|---------------|
| `backend/pyproject.toml` | uv dependencies, pytest config |
| `backend/app/__init__.py` | package marker |
| `backend/app/config.py` | Pydantic Settings — all env vars, no hardcoding |
| `backend/app/main.py` | FastAPI app, CORS middleware, /health endpoint |
| `backend/app/db.py` | SQLAlchemy async engine + session factory + Base |
| `backend/tests/__init__.py` | package marker |
| `backend/tests/conftest.py` | env var overrides so tests run without a real .env |
| `backend/tests/test_config.py` | Settings load, property, and db module import tests |
| `backend/tests/test_health.py` | /health endpoint tests via httpx AsyncClient |
| `backend/.env.example` | documented env var template (committed, no secrets) |
| `backend/Dockerfile` | production container, uv, layer caching |
| `docker-compose.yml` | local dev stack: postgres + ollama |
| `init.sql` | pgvector extension + conversations + documents tables |

---

## Task 1: Directory structure + pyproject.toml

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/tests/__init__.py`

- [ ] **Step 1: Create directory structure**

Run from repo root (`rmerge-AE/`):

```bash
mkdir -p backend/app backend/tests backend/knowledge_base
touch backend/app/__init__.py backend/tests/__init__.py
```

- [ ] **Step 2: Create `backend/pyproject.toml`**

```toml
[project]
name = "automate-this-backend"
version = "0.1.0"
description = "SMB automation advisor — FastAPI backend"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy[asyncio]>=2.0.0",
    "asyncpg>=0.30.0",
    "pydantic-settings>=2.6.0",
    "python-dotenv>=1.0.0",
]

[tool.uv]
dev-dependencies = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.28.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Install dependencies with uv**

```bash
cd backend
uv sync
```

Expected: `uv.lock` created, `.venv/` created, no errors.

- [ ] **Step 4: Verify Python version**

```bash
uv run python --version
```

Expected: `Python 3.12.x`

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/app/__init__.py backend/tests/__init__.py backend/uv.lock
git commit -m "chore: init backend scaffold with uv + FastAPI dependencies"
```

---

## Task 2: Pydantic Settings config

**Files:**
- Create: `backend/app/config.py`
- Create: `backend/.env.example`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_config.py`

- [ ] **Step 1: Write the failing test first**

Create `backend/tests/test_config.py`:

```python
from app.config import settings


def test_settings_has_openrouter_api_key():
    assert settings.openrouter_api_key == "test-key-for-ci"


def test_settings_has_database_url():
    assert "postgresql" in settings.database_url


def test_settings_default_chat_model():
    assert settings.chat_model == "openai/gpt-4o-mini"


def test_settings_allowed_origins_list_is_a_list():
    origins = settings.allowed_origins_list
    assert isinstance(origins, list)
    assert len(origins) >= 1


def test_settings_max_turns_default():
    assert settings.max_turns_per_session == 8
```

- [ ] **Step 2: Create `backend/tests/conftest.py`**

Pydantic Settings reads env vars at instantiation time. conftest.py is collected before test modules, so os.environ set here takes effect before any `from app.config import settings` runs.

```python
import os

# Set required env vars BEFORE app modules are imported.
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-ci")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://automate:automate@localhost:5432/automate_this",
)
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:3000")
```

- [ ] **Step 3: Run the test — expect ImportError (module missing)**

```bash
cd backend
uv run pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 4: Create `backend/app/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    chat_model: str = "openai/gpt-4o-mini"
    embedding_model: str = "openai/text-embedding-3-small"

    # Database
    database_url: str

    # Security
    admin_api_key: str
    allowed_origins: str = "http://localhost:3000"

    # Rate limiting
    max_turns_per_session: int = 8
    max_input_chars: int = 600
    max_output_tokens: int = 450

    # Local dev Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_embedding_model: str = "nomic-embed-text"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]


settings = Settings()
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
uv run pytest tests/test_config.py -v
```

Expected:
```
tests/test_config.py::test_settings_has_openrouter_api_key PASSED
tests/test_config.py::test_settings_has_database_url PASSED
tests/test_config.py::test_settings_default_chat_model PASSED
tests/test_config.py::test_settings_allowed_origins_list_is_a_list PASSED
tests/test_config.py::test_settings_max_turns_default PASSED

5 passed
```

- [ ] **Step 6: Create `backend/.env.example`**

```bash
# LLM — get key at https://openrouter.ai/keys
OPENROUTER_API_KEY=your_openrouter_key_here
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
CHAT_MODEL=openai/gpt-4o-mini
EMBEDDING_MODEL=openai/text-embedding-3-small

# Database
# Local dev: postgresql+asyncpg://automate:automate@localhost:5432/automate_this
# Production: Supabase → Settings → Database → URI connection string
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dbname

# Security
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
ADMIN_API_KEY=generate_a_random_32byte_hex_here
ALLOWED_ORIGINS=http://localhost:3000

# Rate limiting (defaults shown — override to tune)
MAX_TURNS_PER_SESSION=8
MAX_INPUT_CHARS=600
MAX_OUTPUT_TOKENS=450

# Local dev Ollama (only needed when running Ollama locally)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/config.py backend/.env.example backend/tests/conftest.py backend/tests/test_config.py
git commit -m "feat: add Pydantic Settings config with env var validation"
```

---

## Task 3: FastAPI app + /health endpoint (TDD)

**Files:**
- Create: `backend/app/main.py`
- Create: `backend/tests/test_health.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_health.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_returns_200():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_returns_ok_status():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_health_returns_version():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    data = response.json()
    assert "version" in data
    assert isinstance(data["version"], str)


@pytest.mark.asyncio
async def test_health_requires_no_auth():
    """Health check must work without any API key — used by Render uptime monitor."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
```

- [ ] **Step 2: Run test — expect ImportError**

```bash
uv run pytest tests/test_health.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Create `backend/app/main.py`**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

app = FastAPI(
    title="Automate This API",
    description="SMB automation advisor — AI-powered consulting chatbot backend",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Uptime check — no auth required. Render calls this to verify the service is up."""
    return {"status": "ok", "version": app.version}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
uv run pytest tests/test_health.py -v
```

Expected:
```
tests/test_health.py::test_health_returns_200 PASSED
tests/test_health.py::test_health_returns_ok_status PASSED
tests/test_health.py::test_health_returns_version PASSED
tests/test_health.py::test_health_requires_no_auth PASSED

4 passed
```

- [ ] **Step 5: Run all tests together**

```bash
uv run pytest -v
```

Expected: 9 passed, 0 failed.

- [ ] **Step 6: Smoke test the running server**

Terminal 1:
```bash
uv run uvicorn app.main:app --reload --port 8000
```

Terminal 2:
```bash
curl http://localhost:8000/health
```

Expected: `{"status":"ok","version":"0.1.0"}`

Also open `http://localhost:8000/docs` in a browser — Swagger UI should load with the /health endpoint listed under the `meta` tag.

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/tests/test_health.py
git commit -m "feat: add FastAPI app with /health endpoint and CORS middleware"
```

---

## Task 4: SQLAlchemy async DB engine

**Files:**
- Create: `backend/app/db.py`
- Modify: `backend/tests/test_config.py`

Note: We verify the engine is constructed without error. We do NOT connect to a live database in this session — integration tests against real Postgres come in Session 4.

- [ ] **Step 1: Add DB import test to existing test file**

Append to the bottom of `backend/tests/test_config.py`:

```python
def test_db_module_imports_and_engine_exists():
    """Engine construction from a valid DATABASE_URL format must not raise."""
    from app.db import AsyncSessionLocal, engine  # noqa: F401 — import is the assertion

    assert engine is not None
    assert AsyncSessionLocal is not None
```

- [ ] **Step 2: Run test — expect ImportError**

```bash
uv run pytest tests/test_config.py::test_db_module_imports_and_engine_exists -v
```

Expected: `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Create `backend/app/db.py`**

```python
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,          # Set True locally to log SQL queries; keep False in prod
    pool_pre_ping=True,  # Drop stale connections before use — prevents 500s after idle
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models in this project."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session, guarantees close on exit."""
    async with AsyncSessionLocal() as session:
        yield session
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
uv run pytest tests/test_config.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_config.py
git commit -m "feat: add SQLAlchemy async engine, session factory, and Base model class"
```

---

## Task 5: Docker Compose + init.sql

**Files:**
- Create: `docker-compose.yml` (repo root)
- Create: `init.sql` (repo root)

- [ ] **Step 1: Create `init.sql`**

```sql
-- Enable pgvector extension (required before VECTOR columns can be created)
CREATE EXTENSION IF NOT EXISTS vector;

-- Conversation tracking and per-session rate limiting
CREATE TABLE IF NOT EXISTS conversations (
    session_id  UUID        PRIMARY KEY,
    turn_count  INTEGER     NOT NULL DEFAULT 0,
    history     JSONB       NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RAG document store (static patterns + live docs share this table)
CREATE TABLE IF NOT EXISTS documents (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title        TEXT        NOT NULL,
    content      TEXT        NOT NULL,
    embedding    VECTOR(1536),             -- matches text-embedding-3-small dimensions
    metadata     JSONB       NOT NULL DEFAULT '{}',  -- source, fetched_at, use_case etc.
    content_hash TEXT        UNIQUE        -- sha256 of content; enables idempotent re-ingest
);

-- IVFFlat index for approximate cosine similarity search
-- lists=10 is appropriate for our small KB (~100 documents); increase when KB grows past 10k
CREATE INDEX IF NOT EXISTS documents_embedding_idx
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);
```

- [ ] **Step 2: Create `docker-compose.yml`**

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

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama

volumes:
  postgres_data:
  ollama_data:
```

- [ ] **Step 3: Start the local dev stack**

Run from repo root:

```bash
docker compose up -d
```

Wait 10 seconds for Postgres to initialise, then:

```bash
docker compose ps
```

Expected: both `postgres` and `ollama` show `running` status.

- [ ] **Step 4: Verify pgvector extension and tables exist**

```bash
docker compose exec postgres psql -U automate -d automate_this -c "\dt"
```

Expected:
```
         List of relations
 Schema |    Name       | Type  |  Owner
--------+---------------+-------+---------
 public | conversations | table | automate
 public | documents     | table | automate
(2 rows)
```

Verify pgvector is active:

```bash
docker compose exec postgres psql -U automate -d automate_this -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"
```

Expected: one row with `vector`.

- [ ] **Step 5: Wire up local .env**

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env` and set these three values (leave the rest as-is):

```
DATABASE_URL=postgresql+asyncpg://automate:automate@localhost:5432/automate_this
OPENROUTER_API_KEY=<your real OpenRouter key>
ADMIN_API_KEY=<any random string for local dev, e.g. localdev123>
```

- [ ] **Step 6: Full local smoke test**

Terminal 1 (from `backend/`):
```bash
uv run uvicorn app.main:app --reload --port 8000
```

Terminal 2:
```bash
curl http://localhost:8000/health
```

Expected: `{"status":"ok","version":"0.1.0"}`

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml init.sql
git commit -m "feat: add Docker Compose local dev stack and pgvector DB schema"
```

Note: `backend/.env` is in `.gitignore` and must NOT be committed.

---

## Task 6: Dockerfile

**Files:**
- Create: `backend/Dockerfile`

- [ ] **Step 1: Create `backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim

# Install uv — Astral's fast Python package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests first so Docker re-uses this layer on code-only changes
COPY pyproject.toml uv.lock* ./

# Install production dependencies only (no pytest, httpx, etc.)
RUN uv sync --no-dev --frozen

# Copy application source
COPY app/ ./app/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Build the image**

```bash
cd backend
docker build -t automate-this-backend:dev .
```

Expected: build completes without errors. Last line contains `Successfully built` or similar.

- [ ] **Step 3: Run the container locally**

```bash
docker run --rm \
  -e OPENROUTER_API_KEY=test \
  -e DATABASE_URL="postgresql+asyncpg://automate:automate@host.docker.internal:5432/automate_this" \
  -e ADMIN_API_KEY=test \
  -p 8001:8000 \
  automate-this-backend:dev
```

In another terminal:
```bash
curl http://localhost:8001/health
```

Expected: `{"status":"ok","version":"0.1.0"}`

Stop the container with Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add backend/Dockerfile
git commit -m "feat: add production Dockerfile with uv and layer caching"
```

---

## Task 7: Final test run + session wrap-up

- [ ] **Step 1: Run the full test suite**

```bash
cd backend
uv run pytest -v
```

Expected:
```
tests/test_config.py::test_settings_has_openrouter_api_key PASSED
tests/test_config.py::test_settings_has_database_url PASSED
tests/test_config.py::test_settings_default_chat_model PASSED
tests/test_config.py::test_settings_allowed_origins_list_is_a_list PASSED
tests/test_config.py::test_settings_max_turns_default PASSED
tests/test_config.py::test_db_module_imports_and_engine_exists PASSED
tests/test_health.py::test_health_returns_200 PASSED
tests/test_health.py::test_health_returns_ok_status PASSED
tests/test_health.py::test_health_returns_version PASSED
tests/test_health.py::test_health_requires_no_auth PASSED

10 passed in 0.XXs
```

- [ ] **Step 2: Verify Swagger UI**

With `uv run uvicorn app.main:app --reload` running, open:
`http://localhost:8000/docs`

Confirm: Swagger UI loads, `/health` is listed under `meta` tag, clicking "Try it out" → "Execute" returns `{"status":"ok","version":"0.1.0"}`.

- [ ] **Step 3: Manual tasks before Session 2**

These are things only you can do (account creation, credentials):

1. **Supabase:** Create a free project at https://supabase.com
2. **Enable pgvector in Supabase:** Go to SQL Editor → run `CREATE EXTENSION IF NOT EXISTS vector;`
3. **Run init.sql in Supabase:** SQL Editor → paste contents of `init.sql` → run
4. **Get Supabase DATABASE_URL:** Settings → Database → Connection string → URI mode → copy (use `postgresql+asyncpg://` prefix)
5. **Render account:** Sign up at https://render.com (free tier for web services)
6. **Vercel account:** Sign up at https://vercel.com (free tier for Next.js)

Session 2 will build the knowledge base markdown files and the RAG ingestion pipeline.
