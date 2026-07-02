# Session 3 (revised) — Tools + LangChain Retrofit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap between `docs/session-3-chat-mcp.md` (already partially built: `/chat` + `capture_lead` MCP server) and Sprint 2's core requirements — "≥3 tool calls" and "Use LangChain with OpenRouter" — by adding two more LLM-callable tools and retrofitting `/chat` to call the LLM through LangChain instead of the raw OpenAI SDK.

**Why this plan exists:** The original approved design (`docs/superpowers/specs/2026-06-05-automate-this-design.md`) called for 3 tools (`calculate_roi`, `search_automation_patterns`, `suggest_tool_stack`) via a LangChain agent. `docs/session-3-chat-mcp.md` — a later, different plan — built `/chat` with RAG baked in (not tool-driven) and only one LLM-callable tool (`capture_lead`, not in the original spec), with no LangChain. Both Task 1 (`/chat`) and Task 2 (`capture_lead` MCP server) from that plan are already implemented and reviewed; this plan revises Task 1's chat flow and supersedes that plan's Task 3 (tool wiring). Its Tasks 4 (Gradio) and 5 (final verification) are carried forward here, adjusted for the new architecture.

**Architecture:** `/chat` builds a LangChain `ChatOpenAI` client (OpenRouter-backed) bound to 3 explicit OpenAI-format tool schemas: `search_automation_patterns` (RAG retrieval, now LLM-invoked instead of always-run), `calculate_roi` (pure Python, deterministic), and `capture_lead` (already-built FastMCP tool, imported directly). The endpoint runs a bounded 2-call tool loop: first `ainvoke` may return tool calls, which are executed and fed back as `ToolMessage`s, then a second `ainvoke` produces the final natural-language reply. No LangChain `AgentExecutor` — the tool dispatch loop stays explicit and inspectable, keeping the retrofit moderate rather than a full agent rewrite.

**Tech Stack:** `langchain-openai` (`ChatOpenAI`), `langchain-core` (message types), existing FastAPI/SQLAlchemy/asyncpg stack, `uv`.

## Global Constraints

- Package manager: `uv` only — never `pip install`. Add new deps to `pyproject.toml`, then `uv sync`.
- **Known issue in this checkout:** `uv run pytest` fails with `error: uv trampoline failed to canonicalize script path`. Use `uv run python -m pytest ...` instead.
- Settings: always call `get_settings()` — never instantiate `Settings()` directly.
- Async everywhere: all DB calls use `AsyncSessionLocal` + `await`.
- SQL params: `CAST(:x AS vector)` / `CAST(:x AS jsonb)` for parameterized vector/jsonb values — never `::vector`. Literal defaults like `'[]'` in an INSERT don't need this.
- No ORM models: raw `text()` SQL.
- Test style: `pytest-asyncio`, `asyncio_mode = "auto"` (already configured).
- Embeddings: only `text-embedding-3-small` via OpenRouter (1536-dim), via the existing `embed_text`.
- LLM: `openai/gpt-4o-mini` via OpenRouter (`settings.chat_model`), now called through `langchain_openai.ChatOpenAI(model=..., api_key=settings.openrouter_api_key, base_url=settings.openrouter_base_url)`.
- YAGNI: exactly 3 tools, one bounded 2-call tool loop, no `AgentExecutor`, no token/cost tracking, no response-schema changes beyond what's specified below.
- Comments: only when WHY is non-obvious.
- Do NOT modify: `rag/ingest.py`, `rag/retriever.py`, `rag/live_ingester.py`, `init.sql`, `docker-compose.yml`.
- `ChatResponse` keeps its existing shape: `session_id: str`, `reply: str`, `sources: list[dict]`, `turns_remaining: int`. `sources` is now `[]` on turns where `search_automation_patterns` wasn't called (this is a deliberate, correct behavior change from the old always-run RAG — the old behavior returned irrelevant "sources" on every message).

---

## File Map

| File | Responsibility |
|------|---------------|
| `backend/mcp_server/server.py` | Modify — isolate note-creation failure from person-creation success |
| `backend/app/tools/__init__.py` | Create (empty) — package marker |
| `backend/app/tools/roi.py` | Create — `calculate_roi` pure function |
| `backend/app/tools/search.py` | Create — `search_automation_patterns`, wraps existing `embed_text` + `search_documents` |
| `backend/app/main.py` | Modify — replace `AsyncOpenAI` with LangChain `ChatOpenAI`, bind 3 tools, remove always-run RAG |
| `backend/tests/test_mcp_server.py` | Modify — add note-failure-isolation test |
| `backend/tests/test_tools_roi.py` | Create |
| `backend/tests/test_tools_search.py` | Create |
| `backend/tests/test_chat.py` | Modify — rewrite LLM mocking for `ChatOpenAI`, update/add tool-calling tests |
| `backend/pyproject.toml` | Modify — add `langchain-openai`, `langchain-core` |
| `frontend/app.py` | Create — Gradio chat UI (unchanged from `docs/session-3-chat-mcp.md` Task 4) |
| `backend/.env`, `backend/.env.example` | Modify — add `TWENTY_API_KEY`, `TWENTY_BASE_URL=http://localhost:3001` |

---

## Task 1: Fix `capture_lead` note-creation error isolation

**Files:**
- Modify: `backend/mcp_server/server.py:41-80`
- Modify: `backend/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `capture_lead(name, email, company, pain_point) -> dict` — same signature and return shape as before (`{"status": "created", "person_id": ...}` or `{"status": "error", "detail": ...}`). Behavior change only: a note-creation failure after a successful person-creation no longer causes `status: "error"`.

### Context

Task review flagged a real bug (Important, confirmed against `docs/session-3-chat-mcp.md`'s own reference code — it's plan-mandated, not implementer scope creep, but still a defect): `capture_lead` wraps both the person-creation POST and the note-creation POST in one `try/except`. If the person POST succeeds (Person now exists in Twenty CRM) but the note POST fails, the function returns `{"status": "error", ...}` — masking a real success as a failure, and risking a duplicate Person on any retry.

### Current code (`backend/mcp_server/server.py:54-80`)

```python
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{TWENTY_BASE_URL}/api/object/people",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            person_id = data.get("data", {}).get("createPerson", {}).get("id", "unknown")

            # Also log the pain point as a note
            await client.post(
                f"{TWENTY_BASE_URL}/api/object/notes",
                json={
                    "title": f"Automation pain point — {company}",
                    "body": pain_point,
                    "noteTargets": [{"personId": person_id}] if person_id != "unknown" else [],
                },
                headers=headers,
            )
            return {"status": "created", "person_id": person_id}
        except httpx.HTTPStatusError as e:
            return {"status": "error", "detail": str(e)}
        except Exception as e:
            return {"status": "error", "detail": str(e)}
```

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_mcp_server.py`:

```python
@pytest.mark.asyncio
async def test_capture_lead_note_failure_does_not_mask_person_creation():
    """If note creation fails after Person creation succeeds, status is still 'created'."""
    from mcp_server.server import capture_lead

    mock_person_response = MagicMock()
    mock_person_response.raise_for_status = MagicMock()
    mock_person_response.json.return_value = {"data": {"createPerson": {"id": "person-123"}}}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        # First POST (people) succeeds, second POST (notes) raises
        mock_client.post = AsyncMock(side_effect=[mock_person_response, Exception("notes endpoint down")])
        mock_client_cls.return_value = mock_client

        result = await capture_lead(
            name="Jane Smith", email="jane@acme.com", company="Acme Plumbing", pain_point="manual invoicing"
        )

    assert result == {"status": "created", "person_id": "person-123"}
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend
uv run python -m pytest tests/test_mcp_server.py::test_capture_lead_note_failure_does_not_mask_person_creation -v
```

Expected: FAIL — current code returns `{"status": "error", "detail": "notes endpoint down"}` because the note POST's exception is caught by the same `except` block as the person POST.

- [ ] **Step 3: Implement the fix**

Replace `backend/mcp_server/server.py:54-80` with:

```python
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{TWENTY_BASE_URL}/api/object/people",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            person_id = data.get("data", {}).get("createPerson", {}).get("id", "unknown")
        except httpx.HTTPStatusError as e:
            return {"status": "error", "detail": str(e)}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

        try:
            # Best-effort: a failed note shouldn't erase a successful Person creation.
            await client.post(
                f"{TWENTY_BASE_URL}/api/object/notes",
                json={
                    "title": f"Automation pain point — {company}",
                    "body": pain_point,
                    "noteTargets": [{"personId": person_id}] if person_id != "unknown" else [],
                },
                headers=headers,
            )
        except Exception:
            pass

        return {"status": "created", "person_id": person_id}
```

- [ ] **Step 4: Run the new test and the full existing `test_mcp_server.py` suite**

```bash
uv run python -m pytest tests/test_mcp_server.py -v
```

Expected: all 6 tests PASS (5 pre-existing + 1 new). The 3 pre-existing async tests are unaffected: `test_capture_lead_returns_created_on_201` uses the same mock response for both POST calls (still returns `created` with 2 calls made); `test_capture_lead_returns_error_on_http_4xx` and `test_capture_lead_returns_error_on_network_failure` both fail on the *first* (person) POST, so they hit the first `except` block exactly as before.

- [ ] **Step 5: Run the full backend suite to confirm no regressions**

```bash
uv run python -m pytest -m "not integration" -q
```

Expected: 51 passed (unchanged count — this task only changes behavior, not test count net... actually 52, +1 new test), 3 deselected.

- [ ] **Step 6: Commit**

```bash
git add backend/mcp_server/server.py backend/tests/test_mcp_server.py
git commit -m "fix: isolate capture_lead note-creation failure from person-creation success"
```

---

## Task 2: `calculate_roi` tool

**Files:**
- Create: `backend/app/tools/__init__.py` (empty)
- Create: `backend/app/tools/roi.py`
- Create: `backend/tests/test_tools_roi.py`

**Interfaces:**
- Produces: `calculate_roi(hours_saved_per_week: float, hourly_rate: float, setup_cost: float) -> dict` returning `{"annual_savings": float, "payback_weeks": float | None, "three_year_net_savings": float, "decision": str}` where `decision` is one of `"automate"`, `"borderline"`, `"not worth it"`.

### Context

Pure, deterministic Python — no LLM cost, no I/O. This is one of the 3 tools the LLM can call in Task 4's `/chat` retrofit. Decision thresholds: payback ≤ 26 weeks → `"automate"`; 26 < payback ≤ 52 weeks → `"borderline"`; payback > 52 weeks (or the task never pays for itself, i.e. zero/negative weekly savings) → `"not worth it"`.

- [ ] **Step 1: Create the package marker**

```bash
mkdir -p backend/app/tools
touch backend/app/tools/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_tools_roi.py`:

```python
from app.tools.roi import calculate_roi


def test_calculate_roi_returns_expected_keys():
    result = calculate_roi(hours_saved_per_week=5, hourly_rate=30, setup_cost=1000)
    assert set(result.keys()) == {"annual_savings", "payback_weeks", "three_year_net_savings", "decision"}


def test_calculate_roi_annual_savings_calculation():
    # weekly_savings = 5 * 30 = 150; annual = 150 * 52 = 7800
    result = calculate_roi(hours_saved_per_week=5, hourly_rate=30, setup_cost=1000)
    assert result["annual_savings"] == 7800.0


def test_calculate_roi_payback_weeks_calculation():
    # payback = 1000 / 150 = 6.666... -> rounded to 1 decimal
    result = calculate_roi(hours_saved_per_week=5, hourly_rate=30, setup_cost=1000)
    assert result["payback_weeks"] == 6.7


def test_calculate_roi_decision_automate_when_payback_under_26_weeks():
    result = calculate_roi(hours_saved_per_week=5, hourly_rate=30, setup_cost=1000)
    assert result["decision"] == "automate"


def test_calculate_roi_decision_borderline_between_26_and_52_weeks():
    # weekly_savings = 2 * 20 = 40; payback = 1600 / 40 = 40.0
    result = calculate_roi(hours_saved_per_week=2, hourly_rate=20, setup_cost=1600)
    assert result["payback_weeks"] == 40.0
    assert result["decision"] == "borderline"


def test_calculate_roi_decision_not_worth_it_when_payback_over_52_weeks():
    # weekly_savings = 1 * 15 = 15; payback = 5000 / 15 = 333.3...
    result = calculate_roi(hours_saved_per_week=1, hourly_rate=15, setup_cost=5000)
    assert result["decision"] == "not worth it"


def test_calculate_roi_zero_weekly_savings_returns_not_worth_it_with_no_payback():
    result = calculate_roi(hours_saved_per_week=0, hourly_rate=30, setup_cost=1000)
    assert result["decision"] == "not worth it"
    assert result["payback_weeks"] is None


def test_calculate_roi_three_year_net_savings_calculation():
    # three_year_net = 7800 * 3 - 1000 = 22400
    result = calculate_roi(hours_saved_per_week=5, hourly_rate=30, setup_cost=1000)
    assert result["three_year_net_savings"] == 22400.0
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd backend
uv run python -m pytest tests/test_tools_roi.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.tools.roi'`.

- [ ] **Step 4: Implement**

Create `backend/app/tools/roi.py`:

```python
def calculate_roi(hours_saved_per_week: float, hourly_rate: float, setup_cost: float) -> dict:
    """Calculate automation ROI for a small business task.

    Returns annual savings, payback period in weeks, 3-year net savings, and a
    recommendation (automate / borderline / not worth it) based on payback time.
    """
    weekly_savings = hours_saved_per_week * hourly_rate
    annual_savings = weekly_savings * 52

    if weekly_savings <= 0:
        payback_weeks = None
        decision = "not worth it"
    else:
        payback_weeks = round(setup_cost / weekly_savings, 1)
        if payback_weeks <= 26:
            decision = "automate"
        elif payback_weeks <= 52:
            decision = "borderline"
        else:
            decision = "not worth it"

    three_year_net_savings = round((annual_savings * 3) - setup_cost, 2)

    return {
        "annual_savings": round(annual_savings, 2),
        "payback_weeks": payback_weeks,
        "three_year_net_savings": three_year_net_savings,
        "decision": decision,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_tools_roi.py -v
```

Expected: 8 passed.

- [ ] **Step 6: Run the full backend suite**

```bash
uv run python -m pytest -m "not integration" -q
```

Expected: 60 passed (52 + 8 new), 3 deselected.

- [ ] **Step 7: Commit**

```bash
git add backend/app/tools/__init__.py backend/app/tools/roi.py backend/tests/test_tools_roi.py
git commit -m "feat: add calculate_roi tool"
```

---

## Task 3: `search_automation_patterns` tool

**Files:**
- Create: `backend/app/tools/search.py`
- Create: `backend/tests/test_tools_search.py`

**Interfaces:**
- Consumes: `app.rag.ingest.embed_text(content: str) -> list[float]`, `app.rag.retriever.search_documents(query_embedding: list[float], top_k: int = 2) -> list[dict]` (each dict has `title`, `content`, `metadata`, `similarity`) — both already exist, unmodified.
- Produces: `search_automation_patterns(task_description: str) -> dict` returning `{"formatted": str, "sources": list[dict]}` where `formatted` is LLM-facing context text and `sources` is `[{"title": str, "similarity": float}, ...]` for the API response.

### Context

This converts RAG retrieval from the always-run, baked-in-prompt behavior of the current `/chat` (built in the earlier, already-reviewed Task 1 of `docs/session-3-chat-mcp.md`) into an explicit tool the LLM decides to call — matching the original design spec's `search_automation_patterns` tool. Task 4 wires this into `/chat`'s tool loop; this task only builds and unit-tests the function in isolation.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_tools_search.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest


def _fake_docs(n=3):
    return [
        {"title": f"Doc {i}", "content": f"content {i}" * 20, "metadata": {}, "similarity": 0.9 - i * 0.1}
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_search_automation_patterns_returns_formatted_and_sources():
    from app.tools.search import search_automation_patterns

    with (
        patch("app.tools.search.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch("app.tools.search.search_documents", new=AsyncMock(return_value=_fake_docs(3))),
    ):
        result = await search_automation_patterns("How do I automate invoicing?")

    assert set(result.keys()) == {"formatted", "sources"}
    assert len(result["sources"]) == 3
    for source in result["sources"]:
        assert set(source.keys()) == {"title", "similarity"}


@pytest.mark.asyncio
async def test_search_automation_patterns_formatted_contains_doc_titles():
    from app.tools.search import search_automation_patterns

    with (
        patch("app.tools.search.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch("app.tools.search.search_documents", new=AsyncMock(return_value=_fake_docs(2))),
    ):
        result = await search_automation_patterns("Automate onboarding")

    assert "Doc 0" in result["formatted"]
    assert "Doc 1" in result["formatted"]


@pytest.mark.asyncio
async def test_search_automation_patterns_calls_embed_text_with_task_description():
    from app.tools.search import search_automation_patterns

    with (
        patch("app.tools.search.embed_text", new=AsyncMock(return_value=[0.0] * 1536)) as mock_embed,
        patch("app.tools.search.search_documents", new=AsyncMock(return_value=[])),
    ):
        await search_automation_patterns("Automate lead capture")

    mock_embed.assert_called_once_with("Automate lead capture")


@pytest.mark.asyncio
async def test_search_automation_patterns_empty_results_returns_empty_lists():
    from app.tools.search import search_automation_patterns

    with (
        patch("app.tools.search.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch("app.tools.search.search_documents", new=AsyncMock(return_value=[])),
    ):
        result = await search_automation_patterns("Something obscure")

    assert result["formatted"] == ""
    assert result["sources"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
uv run python -m pytest tests/test_tools_search.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.tools.search'`.

- [ ] **Step 3: Implement**

Create `backend/app/tools/search.py`:

```python
from app.rag.ingest import embed_text
from app.rag.retriever import search_documents


async def search_automation_patterns(task_description: str) -> dict:
    """Search the knowledge base for automation patterns relevant to a task.

    Returns `formatted` (context text for the LLM) and `sources` (structured
    title + similarity pairs for the API response).
    """
    query_embedding = await embed_text(task_description)
    docs = await search_documents(query_embedding, top_k=3)

    formatted = "\n\n".join(f"### {d['title']}\n{d['content'][:800]}" for d in docs)
    sources = [{"title": d["title"], "similarity": float(d["similarity"])} for d in docs]

    return {"formatted": formatted, "sources": sources}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_tools_search.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the full backend suite**

```bash
uv run python -m pytest -m "not integration" -q
```

Expected: 64 passed (60 + 4 new), 3 deselected.

- [ ] **Step 6: Commit**

```bash
git add backend/app/tools/search.py backend/tests/test_tools_search.py
git commit -m "feat: add search_automation_patterns tool"
```

---

## Task 4: Retrofit `/chat` to LangChain with 3-tool calling

**Files:**
- Modify: `backend/app/main.py` (full rewrite of the `/chat` handler and its imports; `/health` and `/admin/ingest` untouched)
- Modify: `backend/tests/test_chat.py` (full rewrite — LLM mocking strategy changes from `AsyncOpenAI` to `ChatOpenAI`)
- Modify: `backend/pyproject.toml` (add `langchain-openai`, `langchain-core`)

**Interfaces:**
- Consumes: `app.tools.roi.calculate_roi` (Task 2), `app.tools.search.search_automation_patterns` (Task 3), `mcp_server.server.capture_lead` (existing, fixed in Task 1).
- Produces: `POST /chat` — same `ChatRequest`/`ChatResponse` contract as before (see Global Constraints).

### Context

This is the core retrofit: `/chat` currently (from the earlier `docs/session-3-chat-mcp.md` Task 1, already merged) uses `openai.AsyncOpenAI` directly and always runs RAG retrieval before every LLM call, with no tool calling. This task replaces that with `langchain_openai.ChatOpenAI` bound to 3 explicit tool schemas, removes the always-run RAG, and adds a bounded 2-call tool-execution loop (mirrors the pattern `docs/session-3-chat-mcp.md`'s Task 3 sketched for `capture_lead` alone, generalized to 3 tools and handling multiple tool calls in one turn).

`ChatOpenAI.bind_tools()` accepts tool definitions already in OpenAI's `{"type": "function", "function": {...}}` format and passes them through — this plan uses that form directly (not LangChain's `@tool` decorator / automatic schema inference from Python functions) so behavior is explicit and doesn't depend on how `fastmcp`'s `@mcp.tool()` decorator affects `capture_lead`'s introspectable signature.

**Current `backend/app/main.py` in full** (so the diff below is unambiguous):

```python
import json
import uuid
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy import text

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.rag.ingest import embed_text, ingest_static_kb
from app.rag.live_ingester import ingest_live_docs
from app.rag.retriever import search_documents

app = FastAPI(
    title="Automate This API",
    description="SMB automation advisor — AI-powered consulting chatbot backend",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Uptime check — no auth required. Render calls this to verify the service is up."""
    return {"status": "ok", "version": app.version}


@app.post("/admin/ingest", tags=["admin"])
async def admin_ingest(
    source: Literal["static", "live"] = Query(default="static"),
    x_admin_key: str | None = Header(default=None),
) -> dict:
    """Ingest knowledge base documents into pgvector.

    - source=static (default): embeds the 6 curated KB markdown files
    - source=live: fetches and embeds live docs from n8n, Twenty CRM, Make, Zapier, Lovable
    """
    if x_admin_key is None:
        raise HTTPException(status_code=401, detail="X-Admin-Key header required")
    if x_admin_key != get_settings().admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")

    if source == "static":
        result = await ingest_static_kb()
    else:
        result = await ingest_live_docs()

    return {"source": source, "result": result}


class ChatRequest(BaseModel):
    session_id: str | None = None  # UUID string; None = start new session
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    sources: list[dict]  # [{title, similarity}, ...] top_k=3
    turns_remaining: int


@app.post("/chat", tags=["chat"])
async def chat(body: ChatRequest) -> ChatResponse:
    """Single-turn RAG chat over the SMB automation knowledge base (no tool calling yet)."""
    settings = get_settings()

    if len(body.message) > settings.max_input_chars:
        raise HTTPException(422, f"Message exceeds {settings.max_input_chars} chars")

    session_id = body.session_id or str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text("SELECT turn_count, history FROM conversations WHERE session_id = :sid"),
                {"sid": session_id},
            )
        ).fetchone()

        if row is None:
            turn_count = 0
            history = []
            await session.execute(
                text("INSERT INTO conversations (session_id, turn_count, history) VALUES (:sid, 0, '[]')"),
                {"sid": session_id},
            )
            await session.commit()
        else:
            turn_count = row.turn_count
            history = row.history  # already a list (asyncpg deserialises JSONB)

    if turn_count >= settings.max_turns_per_session:
        raise HTTPException(429, "Session turn limit reached")

    query_embedding = await embed_text(body.message)
    docs = await search_documents(query_embedding, top_k=3)

    context = "\n\n".join(f"### {d['title']}\n{d['content'][:800]}" for d in docs)
    system_prompt = (
        "You are 'Automate This', an SMB automation advisor. "
        "Use the context below to recommend automation solutions. "
        "Be concrete: name the tools, estimate hours saved per week, "
        "and suggest a first step the business owner can take today.\n\n"
        f"CONTEXT:\n{context}"
    )

    client = AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages += history
    messages.append({"role": "user", "content": body.message})

    response = await client.chat.completions.create(
        model=settings.chat_model,
        messages=messages,
        max_tokens=settings.max_output_tokens,
    )
    reply = response.choices[0].message.content

    new_history = history + [
        {"role": "user", "content": body.message},
        {"role": "assistant", "content": reply},
    ]
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE conversations SET turn_count = turn_count + 1, "
                "history = CAST(:history AS jsonb) WHERE session_id = :sid"
            ),
            {"history": json.dumps(new_history), "sid": session_id},
        )
        await session.commit()

    sources = [{"title": d["title"], "similarity": float(d["similarity"])} for d in docs]
    return ChatResponse(
        session_id=session_id,
        reply=reply,
        sources=sources,
        turns_remaining=settings.max_turns_per_session - (turn_count + 1),
    )
```

- [ ] **Step 1: Add LangChain dependencies**

In `backend/pyproject.toml`, add to `dependencies`:

```toml
    "langchain-openai>=0.3.0",
    "langchain-core>=0.3.0",
```

```bash
cd backend
uv sync
```

If these floors don't resolve (version drift), use the latest available compatible versions and record the actual resolved versions in your report — don't silently pin something else without noting it.

- [ ] **Step 2: Write the failing tests first**

Replace `backend/tests/test_chat.py` in full with:

```python
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import app


def _mock_db_session(row=None):
    """Build a mock AsyncSessionLocal context manager whose execute() returns
    a MagicMock result with .fetchone() -> row."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_result = MagicMock()
    mock_result.fetchone.return_value = row
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    return mock_session


def _make_ai_message(content=None, tool_calls=None):
    """A minimal stand-in for a LangChain AIMessage: .content and .tool_calls."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    return msg


def _configure_mock_llm(mock_chat_openai_cls, ainvoke_side_effect):
    """Wire a patched `app.main.ChatOpenAI` class mock's .bind_tools().ainvoke chain."""
    mock_chat_openai_cls.return_value.bind_tools.return_value.ainvoke = AsyncMock(
        side_effect=ainvoke_side_effect
    )


async def _post_chat(payload):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/chat", json=payload)


@pytest.mark.asyncio
async def test_chat_new_session_returns_200_with_new_uuid_session_id():
    """POST /chat with no session_id returns 200 with a new UUID session_id."""
    mock_session = _mock_db_session(row=None)
    ai_msg = _make_ai_message(content="Here is your automation advice.")

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
    ):
        _configure_mock_llm(mock_chat_cls, [ai_msg])
        response = await _post_chat({"message": "How can I automate invoicing?"})

    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    uuid.UUID(data["session_id"])


@pytest.mark.asyncio
async def test_chat_existing_session_increments_turn_count():
    """Second call with same session_id reuses the session (turns_remaining decrements)."""
    session_id = str(uuid.uuid4())
    existing_row = MagicMock(turn_count=2, history=[])
    mock_session = _mock_db_session(row=existing_row)
    ai_msg = _make_ai_message(content="Sure, let's automate onboarding.")

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
    ):
        _configure_mock_llm(mock_chat_cls, [ai_msg])
        response = await _post_chat({"session_id": session_id, "message": "Automate onboarding"})

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == session_id
    settings = get_settings()
    assert data["turns_remaining"] == settings.max_turns_per_session - 3


@pytest.mark.asyncio
async def test_chat_message_too_long_returns_422():
    """Message exceeding max_input_chars returns 422."""
    settings = get_settings()
    long_message = "x" * (settings.max_input_chars + 1)

    response = await _post_chat({"message": long_message})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_turn_limit_reached_returns_429():
    """turn_count >= max_turns_per_session returns 429."""
    settings = get_settings()
    session_id = str(uuid.uuid4())
    existing_row = MagicMock(turn_count=settings.max_turns_per_session, history=[])
    mock_session = _mock_db_session(row=existing_row)

    with patch("app.main.AsyncSessionLocal", return_value=mock_session):
        response = await _post_chat({"session_id": session_id, "message": "One more question"})

    assert response.status_code == 429


@pytest.mark.asyncio
async def test_chat_reply_returned_from_llm():
    """The reply field in the response comes from the LLM completion."""
    mock_session = _mock_db_session(row=None)
    expected_reply = "Use Zapier to connect your CRM and email tool."
    ai_msg = _make_ai_message(content=expected_reply)

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
    ):
        _configure_mock_llm(mock_chat_cls, [ai_msg])
        response = await _post_chat({"message": "Help me automate follow-ups"})

    assert response.status_code == 200
    assert response.json()["reply"] == expected_reply


@pytest.mark.asyncio
async def test_chat_sources_empty_when_no_search_tool_called():
    """sources stays [] when the LLM doesn't call search_automation_patterns this turn."""
    mock_session = _mock_db_session(row=None)
    ai_msg = _make_ai_message(content="Sure, tell me more about your process.")

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
    ):
        _configure_mock_llm(mock_chat_cls, [ai_msg])
        response = await _post_chat({"message": "Hi there"})

    assert response.json()["sources"] == []


@pytest.mark.asyncio
async def test_chat_sources_populated_when_search_tool_called():
    """sources contains up to 3 items with title and similarity fields when the LLM calls search."""
    mock_session = _mock_db_session(row=None)
    tool_call = {"name": "search_automation_patterns", "args": {"task_description": "invoicing"}, "id": "call_1"}
    first_msg = _make_ai_message(content=None, tool_calls=[tool_call])
    second_msg = _make_ai_message(content="Based on patterns, use Zapier.")
    fake_sources = [
        {"title": "Pattern A", "similarity": 0.87},
        {"title": "Pattern B", "similarity": 0.81},
        {"title": "Pattern C", "similarity": 0.75},
    ]

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
        patch(
            "app.main.search_automation_patterns",
            new=AsyncMock(return_value={"formatted": "...", "sources": fake_sources}),
        ),
    ):
        _configure_mock_llm(mock_chat_cls, [first_msg, second_msg])
        response = await _post_chat({"message": "What should I automate first?"})

    assert response.status_code == 200
    sources = response.json()["sources"]
    assert sources == fake_sources
    for source in sources:
        assert set(source.keys()) == {"title", "similarity"}


@pytest.mark.asyncio
async def test_chat_turns_remaining_decrements_correctly():
    """turns_remaining = max_turns_per_session - (turn_count + 1)."""
    settings = get_settings()
    session_id = str(uuid.uuid4())
    existing_row = MagicMock(turn_count=0, history=[])
    mock_session = _mock_db_session(row=existing_row)
    ai_msg = _make_ai_message(content="First reply.")

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
    ):
        _configure_mock_llm(mock_chat_cls, [ai_msg])
        response = await _post_chat({"session_id": session_id, "message": "First question"})

    assert response.status_code == 200
    assert response.json()["turns_remaining"] == settings.max_turns_per_session - 1


@pytest.mark.asyncio
async def test_chat_calculate_roi_tool_called_and_result_fed_back():
    """When the LLM calls calculate_roi, it's executed (real function, deterministic) and
    its result is fed back for a second LLM call."""
    mock_session = _mock_db_session(row=None)
    tool_call = {
        "name": "calculate_roi",
        "args": {"hours_saved_per_week": 5, "hourly_rate": 30, "setup_cost": 1000},
        "id": "call_2",
    }
    first_msg = _make_ai_message(content=None, tool_calls=[tool_call])
    second_msg = _make_ai_message(content="You'd save €7,800/year, paying back in about 7 weeks.")

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
    ):
        _configure_mock_llm(mock_chat_cls, [first_msg, second_msg])
        response = await _post_chat(
            {"message": "I spend 5 hours a week on invoicing, I make 30 EUR/hr, setup costs 1000"}
        )

    assert response.status_code == 200
    assert response.json()["reply"] == "You'd save €7,800/year, paying back in about 7 weeks."
    assert mock_chat_cls.return_value.bind_tools.return_value.ainvoke.call_count == 2


@pytest.mark.asyncio
async def test_chat_capture_lead_tool_called_and_result_fed_back():
    """When the LLM calls capture_lead, it's executed and a confirming reply is produced."""
    mock_session = _mock_db_session(row=None)
    tool_call = {
        "name": "capture_lead",
        "args": {
            "name": "Jane Smith",
            "email": "jane@acme.com",
            "company": "Acme Plumbing",
            "pain_point": "manual invoicing",
        },
        "id": "call_3",
    }
    first_msg = _make_ai_message(content=None, tool_calls=[tool_call])
    second_msg = _make_ai_message(content="Thanks Jane, I've saved your details — we'll follow up soon.")

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
        patch("app.main.capture_lead", new=AsyncMock(return_value={"status": "created", "person_id": "abc-123"})),
    ):
        _configure_mock_llm(mock_chat_cls, [first_msg, second_msg])
        response = await _post_chat(
            {"message": "My name is Jane Smith, email jane@acme.com, I run Acme Plumbing, manual invoicing"}
        )

    assert response.status_code == 200
    assert response.json()["reply"] == "Thanks Jane, I've saved your details — we'll follow up soon."


@pytest.mark.asyncio
async def test_chat_capture_lead_error_result_does_not_crash():
    """A capture_lead error result produces a normal 200 response, not a 500."""
    mock_session = _mock_db_session(row=None)
    tool_call = {
        "name": "capture_lead",
        "args": {
            "name": "Jane Smith",
            "email": "jane@acme.com",
            "company": "Acme Plumbing",
            "pain_point": "manual invoicing",
        },
        "id": "call_4",
    }
    first_msg = _make_ai_message(content=None, tool_calls=[tool_call])
    second_msg = _make_ai_message(content="I couldn't save your details right now, but let's continue.")

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
        patch(
            "app.main.capture_lead",
            new=AsyncMock(return_value={"status": "error", "detail": "network error"}),
        ),
    ):
        _configure_mock_llm(mock_chat_cls, [first_msg, second_msg])
        response = await _post_chat(
            {"message": "My name is Jane Smith, email jane@acme.com, I run Acme Plumbing, manual invoicing"}
        )

    assert response.status_code == 200
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd backend
uv run python -m pytest tests/test_chat.py -v
```

Expected: FAIL — `app.main` doesn't yet import `ChatOpenAI`, `search_automation_patterns`, or `calculate_roi`; several tests will error on `AttributeError`/`ImportError` from `patch("app.main.ChatOpenAI", ...)` etc.

- [ ] **Step 4: Implement — replace `backend/app/main.py` in full**

```python
import json
import uuid
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from sqlalchemy import text

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.rag.ingest import ingest_static_kb
from app.rag.live_ingester import ingest_live_docs
from app.tools.roi import calculate_roi
from app.tools.search import search_automation_patterns
from mcp_server.server import capture_lead

app = FastAPI(
    title="Automate This API",
    description="SMB automation advisor — AI-powered consulting chatbot backend",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Uptime check — no auth required. Render calls this to verify the service is up."""
    return {"status": "ok", "version": app.version}


@app.post("/admin/ingest", tags=["admin"])
async def admin_ingest(
    source: Literal["static", "live"] = Query(default="static"),
    x_admin_key: str | None = Header(default=None),
) -> dict:
    """Ingest knowledge base documents into pgvector.

    - source=static (default): embeds the 6 curated KB markdown files
    - source=live: fetches and embeds live docs from n8n, Twenty CRM, Make, Zapier, Lovable
    """
    if x_admin_key is None:
        raise HTTPException(status_code=401, detail="X-Admin-Key header required")
    if x_admin_key != get_settings().admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")

    if source == "static":
        result = await ingest_static_kb()
    else:
        result = await ingest_live_docs()

    return {"source": source, "result": result}


class ChatRequest(BaseModel):
    session_id: str | None = None  # UUID string; None = start new session
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    sources: list[dict]  # [{title, similarity}, ...] — populated only if search_automation_patterns was called
    turns_remaining: int


CALCULATE_ROI_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate_roi",
        "description": (
            "Calculate the ROI of automating a manual task. Call this when the user states "
            "how many hours per week a task takes and you know or can reasonably estimate "
            "their hourly rate and a rough automation setup cost."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hours_saved_per_week": {"type": "number", "description": "Hours per week the automation would save"},
                "hourly_rate": {"type": "number", "description": "The business owner's hourly rate in EUR"},
                "setup_cost": {"type": "number", "description": "One-time cost to build/set up the automation in EUR"},
            },
            "required": ["hours_saved_per_week", "hourly_rate", "setup_cost"],
        },
    },
}

SEARCH_AUTOMATION_PATTERNS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_automation_patterns",
        "description": (
            "Search the knowledge base for automation patterns relevant to a task the user "
            "described. Call this before recommending a specific automation approach so your "
            "answer is grounded in real patterns rather than guessed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "The manual task or business process to find automation patterns for",
                },
            },
            "required": ["task_description"],
        },
    },
}

CAPTURE_LEAD_TOOL = {
    "type": "function",
    "function": {
        "name": "capture_lead",
        "description": (
            "Save the user's contact details into the CRM so the team can follow up. "
            "Only call this when the user has explicitly provided their name AND email."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full name"},
                "email": {"type": "string", "description": "Email address"},
                "company": {"type": "string", "description": "Business name"},
                "pain_point": {"type": "string", "description": "The automation problem they described"},
            },
            "required": ["name", "email", "company", "pain_point"],
        },
    },
}

SYSTEM_PROMPT = (
    "You are 'Automate This', an SMB automation advisor. You have three tools available: "
    "search_automation_patterns to ground your advice in real automation patterns, "
    "calculate_roi to estimate payback time and savings once you know hours saved, hourly "
    "rate, and setup cost, and capture_lead to save the user's contact details once they've "
    "explicitly given you their name AND email. Be concrete: name the tools, estimate hours "
    "saved per week, and suggest a first step the business owner can take today."
)


@app.post("/chat", tags=["chat"])
async def chat(body: ChatRequest) -> ChatResponse:
    """Multi-turn chat with tool calling (search_automation_patterns, calculate_roi, capture_lead) via LangChain."""
    settings = get_settings()

    if len(body.message) > settings.max_input_chars:
        raise HTTPException(422, f"Message exceeds {settings.max_input_chars} chars")

    session_id = body.session_id or str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text("SELECT turn_count, history FROM conversations WHERE session_id = :sid"),
                {"sid": session_id},
            )
        ).fetchone()

        if row is None:
            turn_count = 0
            history = []
            await session.execute(
                text("INSERT INTO conversations (session_id, turn_count, history) VALUES (:sid, 0, '[]')"),
                {"sid": session_id},
            )
            await session.commit()
        else:
            turn_count = row.turn_count
            history = row.history  # already a list (asyncpg deserialises JSONB)

    if turn_count >= settings.max_turns_per_session:
        raise HTTPException(429, "Session turn limit reached")

    llm = ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        max_tokens=settings.max_output_tokens,
    ).bind_tools([CALCULATE_ROI_TOOL, SEARCH_AUTOMATION_PATTERNS_TOOL, CAPTURE_LEAD_TOOL])

    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    for turn in history:
        if turn["role"] == "user":
            messages.append(HumanMessage(content=turn["content"]))
        else:
            messages.append(AIMessage(content=turn["content"]))
    messages.append(HumanMessage(content=body.message))

    ai_message = await llm.ainvoke(messages)
    sources: list[dict] = []

    if ai_message.tool_calls:
        messages.append(ai_message)
        for tool_call in ai_message.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            if tool_name == "calculate_roi":
                tool_result = calculate_roi(**tool_args)
            elif tool_name == "search_automation_patterns":
                search_result = await search_automation_patterns(**tool_args)
                sources = search_result["sources"]
                tool_result = search_result["formatted"]
            elif tool_name == "capture_lead":
                tool_result = await capture_lead(**tool_args)
            else:
                tool_result = {"status": "error", "detail": f"Unknown tool {tool_name}"}

            messages.append(
                ToolMessage(content=json.dumps(tool_result), tool_call_id=tool_call["id"])
            )

        final_message = await llm.ainvoke(messages)
        reply = final_message.content
    else:
        reply = ai_message.content

    new_history = history + [
        {"role": "user", "content": body.message},
        {"role": "assistant", "content": reply},
    ]
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE conversations SET turn_count = turn_count + 1, "
                "history = CAST(:history AS jsonb) WHERE session_id = :sid"
            ),
            {"history": json.dumps(new_history), "sid": session_id},
        )
        await session.commit()

    return ChatResponse(
        session_id=session_id,
        reply=reply,
        sources=sources,
        turns_remaining=settings.max_turns_per_session - (turn_count + 1),
    )
```

Note: this drops `embed_text` and `search_documents` as direct imports in `main.py` (they're now only used inside `app.tools.search`), and drops the `AsyncOpenAI` import entirely.

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_chat.py -v
```

Expected: 11 passed.

- [ ] **Step 6: Run the full backend suite**

```bash
uv run python -m pytest -m "not integration" -q
```

Expected: 68 passed (64 - 7 old test_chat tests + 11 new test_chat tests = 68), 3 deselected. If the count differs, account for the exact delta before proceeding — don't guess.

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/tests/test_chat.py backend/pyproject.toml backend/uv.lock
git commit -m "feat: retrofit /chat to LangChain with 3-tool calling (search, calculate_roi, capture_lead)"
```

---

## Task 5: Gradio frontend

**Files:**
- Create: `frontend/app.py`
- Modify: `backend/pyproject.toml` (add `gradio>=4.0` to `dependencies`) — `frontend/` is not its own uv project; run the script with `uv run python frontend/app.py` from `backend/`, or from the repo root with `uv run --project backend python frontend/app.py`.

**Interfaces:**
- Consumes: `POST http://localhost:8000/chat` — same `ChatRequest`/`ChatResponse` contract as Task 4 (unchanged by this task).

### Context

This task is unchanged from `docs/session-3-chat-mcp.md`'s Task 4 — the `/chat` response contract (`session_id`, `reply`, `sources`, `turns_remaining`) is the same after the Task 4 retrofit above, so this Gradio UI needs no adaptation for the new tool-calling architecture.

- [ ] **Step 1: Add the Gradio dependency**

Add to `backend/pyproject.toml` `dependencies`:

```toml
    "gradio>=4.0",
```

```bash
cd backend
uv sync
```

- [ ] **Step 2: Create the Gradio app**

Create `frontend/app.py`:

```python
"""
Gradio chat frontend for Automate This.
Run with: uv run python frontend/app.py
"""
import uuid
import httpx
import gradio as gr

API_URL = "http://localhost:8000/chat"
session_id = str(uuid.uuid4())


def chat(message: str, history: list) -> tuple[str, list]:
    global session_id
    try:
        resp = httpx.post(
            API_URL,
            json={"session_id": session_id, "message": message},
            timeout=30.0,
        )
        data = resp.json()
        if resp.status_code == 429:
            return "Session limit reached. Please refresh to start a new conversation.", history
        if resp.status_code != 200:
            return f"Error {resp.status_code}: {data.get('detail', 'Unknown error')}", history

        reply = data["reply"]
        turns_left = data.get("turns_remaining", "?")
        sources = data.get("sources", [])

        if sources:
            citations = "\n\n**Sources used:**\n" + "\n".join(
                f"- {s['title']} (similarity: {s['similarity']:.2f})" for s in sources
            )
            reply += citations

        reply += f"\n\n*{turns_left} turns remaining in this session.*"
        history.append((message, reply))
        return "", history
    except Exception as e:
        return f"Connection error: {e}", history


with gr.Blocks(title="Automate This — SMB Advisor") as demo:
    gr.Markdown("# Automate This\nDescribe a repetitive task your business does manually. I'll tell you how to automate it.")
    chatbot = gr.Chatbot(height=500)
    msg = gr.Textbox(placeholder="e.g. I spend 3 hours a week chasing unpaid invoices by email...", label="Your message")
    clear = gr.Button("New conversation")

    msg.submit(chat, [msg, chatbot], [msg, chatbot])
    clear.click(lambda: (str(uuid.uuid4()), []), outputs=[gr.State(), chatbot])

if __name__ == "__main__":
    demo.launch(server_port=7860)
```

- [ ] **Step 3: Manual verification**

```bash
# Terminal 1
cd backend
uv run uvicorn app.main:app --reload --port 8000

# Terminal 2
uv run python frontend/app.py
```

Open `http://localhost:7860`, send a message, confirm a reply comes back. This is manual (no automated test — Gradio UI has no test harness in this project).

- [ ] **Step 4: Commit**

```bash
git add frontend/app.py backend/pyproject.toml backend/uv.lock
git commit -m "feat: add Gradio chat frontend"
```

---

## Task 6: Final verification + env vars

**Files:**
- Modify: `backend/.env`, `backend/.env.example`

### Steps

- [ ] **Step 1: Add Twenty CRM env vars**

Add to `backend/.env` and `backend/.env.example`:

```bash
# Twenty CRM (for capture_lead MCP tool)
TWENTY_API_KEY=your_twenty_crm_api_key_here
TWENTY_BASE_URL=http://localhost:3001
```

`TWENTY_BASE_URL` is `http://localhost:3001`, not `3000` — confirmed against the actual running local Twenty CRM instance (`http://localhost:3001/objects/companies?...`), not the plan's originally guessed default.

Get the API key from Twenty CRM → Settings → API → Generate new key.

- [ ] **Step 2: Run the full unit suite**

```bash
cd backend
uv run python -m pytest -m "not integration" -q
```

Expected: 68 passed, 3 deselected (see Task 4 Step 6 note on the exact count).

- [ ] **Step 3: Run integration tests (needs live DB)**

```bash
uv run python -m pytest -m integration -v
```

- [ ] **Step 4: Smoke test the full flow manually**

```bash
# Start the API
uv run uvicorn app.main:app --reload --port 8000

# New chat session — should trigger search_automation_patterns
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I spend 4 hours a week manually creating invoices in Word and emailing them"}'

# Ask for ROI — should trigger calculate_roi
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<id from above>", "message": "I make 40 EUR an hour and setup would cost about 500 EUR, is it worth it?"}'

# Provide contact details with a THROWAWAY test lead (not real customer data) — should trigger capture_lead
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<id from above>", "message": "My name is Test User, email test@example.com, I run Test Co"}'
```

Verify via logs or the response `reply` text that each turn actually triggered the expected tool (no direct visibility into tool calls in the response body by design — `sources` being non-empty is the signal for the search tool call).

- [ ] **Step 5: Commit**

```bash
git add backend/.env.example
git commit -m "docs: add Twenty CRM env vars, confirm localhost:3001"
```

(`.env` is gitignored — update it locally but it won't be committed.)

---

## Task 7: Fix `capture_lead` against the real Twenty CRM REST API

**Files:**
- Modify: `backend/mcp_server/server.py`
- Modify: `backend/tests/test_mcp_server.py`
- Modify: `backend/app/main.py:221-224` (empty-reply fallback, unrelated bug found during the same investigation)

**Interfaces:**
- No change to `capture_lead`'s signature or return shape (`{"status": "created", "person_id": ...}` / `{"status": "error", "detail": ...}`).

### Context

Task 6's live smoke test (curl against `http://localhost:8001/chat` with a real OpenRouter key and a real Twenty CRM instance at `http://localhost:3001`) found that `capture_lead` never actually saves a lead — confirmed by direct `curl` investigation against the live Twenty CRM instance, not guesswork:

1. **Wrong base path.** The code POSTs to `{TWENTY_BASE_URL}/api/object/people` and `/api/object/notes`. Both return the frontend SPA's `index.html` (200 OK, `Content-Type: text/html`), which then fails to `.json()`-parse and is caught by the generic `except Exception`. The real, working path (verified via `curl -X POST http://localhost:3001/rest/people ...` returning valid JSON) is `/rest/people`, `/rest/notes`, `/rest/noteTargets`.
2. **`company` field format wrong.** `{"company": {"name": company}}` on the person payload gets `400 Bad Request`: `"Relation \"company\" requires connect or disconnect operation"`. Per the earlier scope decision: **drop this field entirely** — the company name already appears in the note title (`f"Automation pain point — {company}"`), so no Company-record lookup/creation is being added.
3. **`noteTargets` can't be written inline on note creation** — `400 Bad Request`: `"One-to-many relation noteTargets field does not support write operations."` Verified fix: create the note first (`POST /rest/notes` with `bodyV2: {"markdown": pain_point}`, not the old `body: pain_point` string field — plain `body` also errors: `"Object note doesn't have any \"body\" field."`), then link it with a **separate** `POST /rest/noteTargets` using field name `targetPersonId` (not `personId` — verified via Twenty's OpenAPI schema at `/open-api/core`, `NoteTarget.targetPersonId`).

All three were verified against the live instance with real `curl` calls (person create, note create, noteTarget create all returned 200 with the shapes below; test records were deleted afterward). This task encodes those verified shapes — the implementer should not need to hit the live Twenty CRM to build this, only to match the shapes given here exactly.

**Verified request/response shapes (from live testing):**

```
POST {TWENTY_BASE_URL}/rest/people
Body: {"name": {"firstName": ..., "lastName": ...}, "emails": {"primaryEmail": ...}, "jobTitle": "SMB Owner"}
Response: {"data": {"createPerson": {"id": "<uuid>", ...}}}

POST {TWENTY_BASE_URL}/rest/notes
Body: {"title": "Automation pain point — <company>", "bodyV2": {"markdown": "<pain_point>"}}
Response: {"data": {"createNote": {"id": "<uuid>", ...}}}

POST {TWENTY_BASE_URL}/rest/noteTargets
Body: {"noteId": "<note id>", "targetPersonId": "<person id>"}
Response: {"data": {"createNoteTarget": {...}}}
```

Separately, and unrelated to the Twenty CRM investigation: the live smoke test also observed `POST /chat` returning `{"reply": "", ...}` (HTTP 200, not a crash — `reply: str` accepts an empty string) after a failed tool call, because the second `llm.ainvoke()` occasionally returns empty `content`. This is a real UX gap (a silent blank response with no indication anything went wrong) that mocked tests can't catch since they control the mock's content directly. Fix with a minimal fallback.

- [ ] **Step 1: Write the failing tests for the new `capture_lead` behavior**

Replace `backend/tests/test_mcp_server.py` in full with:

```python
"""Tests for the FastMCP capture_lead server."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _mock_response(json_data, raise_on_status=False):
    resp = MagicMock()
    if raise_on_status:
        import httpx
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


@pytest.mark.asyncio
async def test_capture_lead_returns_created_on_success():
    """capture_lead returns {"status": "created", "person_id": ...} when person creation succeeds."""
    from mcp_server.server import capture_lead

    person_resp = _mock_response({"data": {"createPerson": {"id": "person-uuid-123"}}})
    note_resp = _mock_response({"data": {"createNote": {"id": "note-uuid-456"}}})
    target_resp = _mock_response({"data": {"createNoteTarget": {"id": "target-uuid-789"}}})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[person_resp, note_resp, target_resp])
        mock_client_cls.return_value = mock_client

        result = await capture_lead(
            name="Jane Smith",
            email="jane@example.com",
            company="ACME Corp",
            pain_point="Manual data entry is killing us",
        )

    assert result == {"status": "created", "person_id": "person-uuid-123"}


@pytest.mark.asyncio
async def test_capture_lead_posts_to_rest_people_path():
    """The person-creation POST goes to /rest/people, not /api/object/people."""
    from mcp_server.server import capture_lead

    person_resp = _mock_response({"data": {"createPerson": {"id": "person-uuid-123"}}})
    note_resp = _mock_response({"data": {"createNote": {"id": "note-uuid-456"}}})
    target_resp = _mock_response({"data": {"createNoteTarget": {}}})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[person_resp, note_resp, target_resp])
        mock_client_cls.return_value = mock_client

        await capture_lead(name="Jane Smith", email="jane@example.com", company="ACME", pain_point="x")

    first_call_url = mock_client.post.call_args_list[0][0][0]
    assert first_call_url.endswith("/rest/people")


@pytest.mark.asyncio
async def test_capture_lead_person_payload_has_no_company_field():
    """The person-creation payload does not include a company field (Twenty rejects nested company writes)."""
    from mcp_server.server import capture_lead

    person_resp = _mock_response({"data": {"createPerson": {"id": "person-uuid-123"}}})
    note_resp = _mock_response({"data": {"createNote": {"id": "note-uuid-456"}}})
    target_resp = _mock_response({"data": {"createNoteTarget": {}}})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[person_resp, note_resp, target_resp])
        mock_client_cls.return_value = mock_client

        await capture_lead(name="Jane Smith", email="jane@example.com", company="ACME", pain_point="x")

    person_payload = mock_client.post.call_args_list[0][1]["json"]
    assert "company" not in person_payload


@pytest.mark.asyncio
async def test_capture_lead_creates_note_with_bodyV2_markdown():
    """The note-creation payload uses bodyV2.markdown, not a plain body string."""
    from mcp_server.server import capture_lead

    person_resp = _mock_response({"data": {"createPerson": {"id": "person-uuid-123"}}})
    note_resp = _mock_response({"data": {"createNote": {"id": "note-uuid-456"}}})
    target_resp = _mock_response({"data": {"createNoteTarget": {}}})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[person_resp, note_resp, target_resp])
        mock_client_cls.return_value = mock_client

        await capture_lead(name="Jane Smith", email="jane@example.com", company="ACME", pain_point="manual invoicing")

    note_call = mock_client.post.call_args_list[1]
    assert note_call[0][0].endswith("/rest/notes")
    note_payload = note_call[1]["json"]
    assert note_payload["bodyV2"] == {"markdown": "manual invoicing"}
    assert "body" not in note_payload
    assert "ACME" in note_payload["title"]


@pytest.mark.asyncio
async def test_capture_lead_links_note_via_separate_note_targets_call():
    """The note-to-person link is a separate POST to /rest/noteTargets with targetPersonId."""
    from mcp_server.server import capture_lead

    person_resp = _mock_response({"data": {"createPerson": {"id": "person-uuid-123"}}})
    note_resp = _mock_response({"data": {"createNote": {"id": "note-uuid-456"}}})
    target_resp = _mock_response({"data": {"createNoteTarget": {}}})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[person_resp, note_resp, target_resp])
        mock_client_cls.return_value = mock_client

        await capture_lead(name="Jane Smith", email="jane@example.com", company="ACME", pain_point="x")

    assert mock_client.post.call_count == 3
    target_call = mock_client.post.call_args_list[2]
    assert target_call[0][0].endswith("/rest/noteTargets")
    target_payload = target_call[1]["json"]
    assert target_payload == {"noteId": "note-uuid-456", "targetPersonId": "person-uuid-123"}


@pytest.mark.asyncio
async def test_capture_lead_returns_error_on_person_creation_http_4xx():
    """capture_lead returns {"status": "error", ...} when the person-creation POST fails (e.g. bad payload)."""
    from mcp_server.server import capture_lead

    person_resp = _mock_response({}, raise_on_status=True)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=person_resp)
        mock_client_cls.return_value = mock_client

        result = await capture_lead(
            name="Jane Smith", email="jane@example.com", company="ACME Corp", pain_point="x"
        )

    assert result["status"] == "error"
    assert "detail" in result


@pytest.mark.asyncio
async def test_capture_lead_returns_error_on_network_failure():
    """capture_lead returns {"status": "error", ...} on network failure."""
    from mcp_server.server import capture_lead

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client_cls.return_value = mock_client

        result = await capture_lead(
            name="Jane Smith", email="jane@example.com", company="ACME Corp", pain_point="x"
        )

    assert result["status"] == "error"
    assert "detail" in result


@pytest.mark.asyncio
async def test_capture_lead_note_or_link_failure_does_not_mask_person_creation():
    """If note creation or note-linking fails after Person creation succeeds, status is still 'created'."""
    from mcp_server.server import capture_lead

    person_resp = _mock_response({"data": {"createPerson": {"id": "person-123"}}})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[person_resp, Exception("notes endpoint down")])
        mock_client_cls.return_value = mock_client

        result = await capture_lead(
            name="Jane Smith", email="jane@acme.com", company="Acme Plumbing", pain_point="manual invoicing"
        )

    assert result == {"status": "created", "person_id": "person-123"}


def test_capture_lead_splits_two_word_name_correctly():
    """Two-word name "Jane Smith" → firstName="Jane", lastName="Smith"."""
    name = "Jane Smith"
    first, *rest = name.strip().split(" ", 1)
    last = rest[0] if rest else ""

    assert first == "Jane"
    assert last == "Smith"


def test_capture_lead_splits_single_word_name_correctly():
    """Single-word name "Cher" → firstName="Cher", lastName=""."""
    name = "Cher"
    first, *rest = name.strip().split(" ", 1)
    last = rest[0] if rest else ""

    assert first == "Cher"
    assert last == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
uv run python -m pytest tests/test_mcp_server.py -v
```

Expected: several FAIL — the current code still posts to `/api/object/people`/`/api/object/notes`, still sends a `company` field, still sends `body` instead of `bodyV2`, and never makes a 3rd `/rest/noteTargets` call.

- [ ] **Step 3: Implement — replace `backend/mcp_server/server.py` in full**

```python
"""
FastMCP server exposing the capture_lead tool.
Wraps Twenty CRM's REST API (/rest/...) to create a Person record and link a note to it.

Run with:
    uv run python -m mcp_server.server

Or as MCP stdio server (for Gradio / Claude Desktop):
    uv run fastmcp run mcp_server/server.py
"""
import os
import httpx
from fastmcp import FastMCP

mcp = FastMCP("automate-this-lead-capture")

TWENTY_BASE_URL = os.getenv("TWENTY_BASE_URL", "http://localhost:3001")
TWENTY_API_KEY = os.getenv("TWENTY_API_KEY", "")


@mcp.tool()
async def capture_lead(
    name: str,
    email: str,
    company: str,
    pain_point: str,
) -> dict:
    """
    Save a prospective lead into Twenty CRM.

    Args:
        name: Full name of the contact (e.g. "Jane Smith")
        email: Business email address
        company: Company or trading name
        pain_point: The automation problem they described in the chat

    Returns:
        {"status": "created", "person_id": "<uuid>"} on success
        {"status": "error", "detail": "<msg>"} on failure
    """
    headers = {
        "Authorization": f"Bearer {TWENTY_API_KEY}",
        "Content-Type": "application/json",
    }
    first, *rest = name.strip().split(" ", 1)
    last = rest[0] if rest else ""

    payload = {
        "name": {"firstName": first, "lastName": last},
        "emails": {"primaryEmail": email},
        "jobTitle": "SMB Owner",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{TWENTY_BASE_URL}/rest/people",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            person_id = data.get("data", {}).get("createPerson", {}).get("id", "unknown")
        except httpx.HTTPStatusError as e:
            return {"status": "error", "detail": str(e)}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

        try:
            # Best-effort: a failed note (or link) shouldn't erase a successful Person creation.
            note_resp = await client.post(
                f"{TWENTY_BASE_URL}/rest/notes",
                json={
                    "title": f"Automation pain point — {company}",
                    "bodyV2": {"markdown": pain_point},
                },
                headers=headers,
            )
            note_resp.raise_for_status()
            note_id = note_resp.json().get("data", {}).get("createNote", {}).get("id")

            if note_id and person_id != "unknown":
                await client.post(
                    f"{TWENTY_BASE_URL}/rest/noteTargets",
                    json={"noteId": note_id, "targetPersonId": person_id},
                    headers=headers,
                )
        except Exception:
            pass

        return {"status": "created", "person_id": person_id}


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_mcp_server.py -v
```

Expected: 9 passed (7 async + 2 sync).

- [ ] **Step 5: Fix the empty-reply fallback in `/chat`**

In `backend/app/main.py`, find these two lines (around line 221-224):

```python
        final_message = await llm.ainvoke(messages)
        reply = final_message.content
    else:
        reply = ai_message.content
```

Replace with:

```python
        final_message = await llm.ainvoke(messages)
        reply = final_message.content or "I've made a note of that — could you tell me more?"
    else:
        reply = ai_message.content or "Could you tell me more about what you're looking to automate?"
```

- [ ] **Step 6: Add a test for the empty-reply fallback**

Add to `backend/tests/test_chat.py`:

```python
@pytest.mark.asyncio
async def test_chat_empty_llm_content_falls_back_to_default_reply():
    """If the LLM returns empty content after a tool call, a non-empty fallback reply is used."""
    mock_session = _mock_db_session(row=None)
    tool_call = {
        "name": "capture_lead",
        "args": {"name": "Jane", "email": "jane@x.com", "company": "X Co", "pain_point": "x"},
        "id": "call_5",
    }
    first_msg = _make_ai_message(content=None, tool_calls=[tool_call])
    second_msg = _make_ai_message(content="")

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
        patch("app.main.capture_lead", new=AsyncMock(return_value={"status": "created", "person_id": "1"})),
    ):
        _configure_mock_llm(mock_chat_cls, [first_msg, second_msg])
        response = await _post_chat({"message": "My name is Jane, email jane@x.com, I run X Co, x"})

    assert response.status_code == 200
    assert response.json()["reply"] != ""
```

- [ ] **Step 7: Run the full backend suite**

```bash
uv run python -m pytest -m "not integration" -q
```

Expected: 78 passed (68 - 6 old mcp_server tests + 9 new mcp_server tests + 1 new chat test = 72... recompute exactly: prior baseline going into this task is 68 (main suite) + the 6 mcp_server tests already counted within an earlier baseline. Don't guess — run the suite and report the actual before/after counts; the important invariant is: no test outside `test_mcp_server.py` and the one new `test_chat.py` test regresses, and all new/modified tests pass).

- [ ] **Step 8: Commit**

```bash
git add backend/mcp_server/server.py backend/tests/test_mcp_server.py backend/app/main.py backend/tests/test_chat.py
git commit -m "fix: use Twenty CRM's real /rest API paths and payload shapes in capture_lead; add empty-reply fallback"
```

- [ ] **Step 9: Re-run the live smoke test (manual, not automated)**

With the dev server running (`uv run python -m uvicorn app.main:app --port 8001` from `backend/`, adjusting the port if 8000/8001 are occupied) and real `OPENROUTER_API_KEY`/`TWENTY_API_KEY` in `backend/.env`:

```bash
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "My name is Test User, email test@example.com, I run Test Co, manual invoicing"}'
```

Confirm the reply is non-empty and reads like a success confirmation. Then verify in Twenty CRM (via `curl -s http://localhost:3001/rest/people -H "Authorization: Bearer $TWENTY_API_KEY"` or the Twenty CRM UI) that a Person named "Test User" now exists with a linked note. **Delete this test record afterward** (`DELETE http://localhost:3001/rest/people/<id>` and the associated note) so no test junk is left in the real CRM.

---

## Task 8: Fix `TWENTY_API_KEY`/`TWENTY_BASE_URL` never actually loading

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/mcp_server/server.py`
- Modify: `backend/tests/test_mcp_server.py` (only if a test asserts on the Authorization header — see Step 3)

### Context

Task 7's Step 9 re-run of the live smoke test (controller, manual, real credentials) still failed — `capture_lead` returned `{"status": "error", "detail": "Illegal header value b'Bearer '"}`, meaning `TWENTY_API_KEY` was empty at call time even though it's set correctly in `backend/.env`.

Root cause: `backend/mcp_server/server.py` reads these two values via bare `os.getenv("TWENTY_API_KEY", "")` / `os.getenv("TWENTY_BASE_URL", "http://localhost:3001")` at module level. But this project's `.env` file is loaded exclusively through `Settings` (`app/config.py`, `SettingsConfigDict(env_file=".env", ...)`) — pydantic-settings parses `.env` directly into the `Settings` object, it does **not** write those values into `os.environ`. Since `TWENTY_API_KEY`/`TWENTY_BASE_URL` were also never declared as fields on `Settings` in the first place, `os.getenv()` had no path to ever see the real key — this has been broken since the tool was first built (Task 2 of this plan), masked until now because Tasks 6/7's smoke tests were busy surfacing the URL/payload bugs first.

This is exactly the pattern the session's own Global Constraint says to avoid: "Settings: always call `get_settings()` — never instantiate `Settings()` directly" (and by extension, never bypass it with raw `os.getenv()`).

- [ ] **Step 1: Add the two fields to `Settings`**

In `backend/app/config.py`, add after the `# Local dev Ollama` block:

```python
    # Twenty CRM (for capture_lead MCP tool)
    twenty_api_key: str = ""
    twenty_base_url: str = "http://localhost:3001"
```

- [ ] **Step 2: Replace the `os.getenv` calls in `mcp_server/server.py`**

Replace the top of `backend/mcp_server/server.py` (imports and the two module-level constants) so it reads settings via `get_settings()` instead:

```python
"""
FastMCP server exposing the capture_lead tool.
Wraps Twenty CRM's REST API (/rest/...) to create a Person record and link a note to it.

Run with:
    uv run python -m mcp_server.server

Or as MCP stdio server (for Gradio / Claude Desktop):
    uv run fastmcp run mcp_server/server.py
"""
import httpx
from fastmcp import FastMCP

from app.config import get_settings

mcp = FastMCP("automate-this-lead-capture")


@mcp.tool()
async def capture_lead(
    name: str,
    email: str,
    company: str,
    pain_point: str,
) -> dict:
    """
    Save a prospective lead into Twenty CRM.

    Args:
        name: Full name of the contact (e.g. "Jane Smith")
        email: Business email address
        company: Company or trading name
        pain_point: The automation problem they described in the chat

    Returns:
        {"status": "created", "person_id": "<uuid>"} on success
        {"status": "error", "detail": "<msg>"} on failure
    """
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {settings.twenty_api_key}",
        "Content-Type": "application/json",
    }
    first, *rest = name.strip().split(" ", 1)
    last = rest[0] if rest else ""

    payload = {
        "name": {"firstName": first, "lastName": last},
        "emails": {"primaryEmail": email},
        "jobTitle": "SMB Owner",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{settings.twenty_base_url}/rest/people",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            person_id = data.get("data", {}).get("createPerson", {}).get("id", "unknown")
        except httpx.HTTPStatusError as e:
            return {"status": "error", "detail": str(e)}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

        try:
            # Best-effort: a failed note (or link) shouldn't erase a successful Person creation.
            note_resp = await client.post(
                f"{settings.twenty_base_url}/rest/notes",
                json={
                    "title": f"Automation pain point — {company}",
                    "bodyV2": {"markdown": pain_point},
                },
                headers=headers,
            )
            note_resp.raise_for_status()
            note_id = note_resp.json().get("data", {}).get("createNote", {}).get("id")

            if note_id and person_id != "unknown":
                await client.post(
                    f"{settings.twenty_base_url}/rest/noteTargets",
                    json={"noteId": note_id, "targetPersonId": person_id},
                    headers=headers,
                )
        except Exception:
            pass

        return {"status": "created", "person_id": person_id}


if __name__ == "__main__":
    mcp.run()
```

This is the entire file — the only change from Task 7's version is the imports (drop `os`, add `from app.config import get_settings`) and reading `settings.twenty_api_key`/`settings.twenty_base_url` via `get_settings()` inside the function instead of the two module-level `os.getenv(...)` constants. The rest of the function body (payload shapes, endpoint paths, error handling) is unchanged from Task 7.

- [ ] **Step 3: Run the existing test suite — confirm no test depended on the old module-level constants**

```bash
cd backend
uv run python -m pytest tests/test_mcp_server.py -v
```

All 10 tests should still pass unmodified — none of Task 7's tests assert on `TWENTY_API_KEY`/`TWENTY_BASE_URL` or the `Authorization` header value, they only assert on URL path suffixes and JSON payload contents, both of which are unaffected by this change. If any test unexpectedly fails or references the old `TWENTY_API_KEY`/`TWENTY_BASE_URL` module-level names directly (e.g. via `patch("mcp_server.server.TWENTY_API_KEY", ...)`), fix that test to patch `app.config.get_settings` (or `mcp_server.server.get_settings`) instead — but confirm first whether this is actually needed before changing anything.

- [ ] **Step 4: Run the full backend suite**

```bash
uv run python -m pytest -m "not integration" -q
```

Expected: same count as after Task 7 (73), unchanged — this task only fixes where values come from, not test behavior.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/mcp_server/server.py
git commit -m "fix: capture_lead reads TWENTY_API_KEY/TWENTY_BASE_URL via get_settings(), not raw os.getenv"
```

- [ ] **Step 6: Re-run the live smoke test (manual, controller only — not part of the implementer's job)**

Same as Task 7 Step 9. This time the `Authorization` header will carry the real key. Confirm a Person + linked Note actually appear in Twenty CRM, then delete the test record.
