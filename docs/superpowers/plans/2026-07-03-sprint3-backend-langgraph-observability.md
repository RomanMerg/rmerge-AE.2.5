# Sprint 3 Phase 1 — LangGraph Agent + Structured Logging + Langfuse + SSE Streaming

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manual 2-call tool loop in `/chat` with a real LangGraph agent (explicit StateGraph, Postgres checkpointer for memory), add structured JSON logging, Langfuse observability, and an SSE streaming endpoint — the backend foundation for the Sprint 3 submission and the future Next.js frontend.

**Architecture:** An explicit `StateGraph` (agent node → conditional edge → tools node → back to agent) in a new `app/agent.py` module owns the tool-calling loop; a LangGraph `AsyncPostgresSaver` checkpointer (thread_id = session_id) owns conversation history, replacing the hand-rolled JSONB history. The `conversations` table is kept **only** for `turn_count` (per-session rate limiting). `/chat` keeps its exact response contract so the Gradio demo keeps working unchanged; `/chat/stream` is added for the Next.js frontend. Langfuse tracing is env-gated (no keys → no-op).

**Tech Stack:** langgraph ≥1.0, langgraph-checkpoint-postgres, psycopg 3 (checkpointer only — asyncpg stays for everything else), structlog, langfuse (v3 SDK), FastAPI SSE via `StreamingResponse`.

**Out of scope (later plans):** Next.js frontend (Phase 2, own plan), long-term user memory, `suggest_tool_stack` tool, agentic RAG / query reformulation.

## Global Constraints

- Package manager is **uv only** — never `pip install`. Add deps with `uv add` / `uv add --dev` from `backend/`.
- Run tests as `uv run python -m pytest` (bare `uv run pytest` has a broken trampoline in this checkout).
- All commands below run from `backend/` unless stated otherwise.
- Every credential/setting goes through `get_settings()` (pydantic-settings). **Never `os.getenv()`** — this caused a real prior bug (see progress.md Task 8).
- The `POST /chat` request/response contract must not change: request `{session_id?, message}`, response `{session_id, reply, sources, turns_remaining, tokens_used, cost_usd}`. The Gradio demo (`frontend/app.py`) depends on it and is not modified in this plan.
- Existing behavior preserved: 422 on message > `max_input_chars`, per-IP 429 (`check_ip_rate_limit`), per-session 429 after `max_turns_per_session` turns, empty-reply fallback strings, `cost_usd` rounded to 6 decimals.
- Token/cost fields are **per-turn**, not per-session (matches current behavior).
- Commit message style: `feat:` / `fix:` / `docs:` / `chore:` prefixes, imperative mood.
- Python 3.12, `asyncio_mode = "auto"` in pytest (no need for `@pytest.mark.asyncio`, though existing tests use it — either is fine).

## Manual setup (user, before Task 5 is verified live)

- Create a free Langfuse Cloud account at https://cloud.langfuse.com (EU region is fine), create a project, and copy the public + secret keys into `backend/.env` as `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`. Without keys everything still works — tracing is just disabled.

---

### Task 1: Dependencies + settings (Langfuse keys, psycopg URL)

**Files:**
- Modify: `backend/pyproject.toml` (via `uv add`)
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.langfuse_public_key: str` (default `""`), `Settings.langfuse_secret_key: str` (default `""`), `Settings.langfuse_host: str` (default `"https://cloud.langfuse.com"`), `Settings.database_url_psycopg: str` property (asyncpg URL → plain `postgresql://` for psycopg3). Later tasks rely on these exact names.

- [ ] **Step 1: Add dependencies**

```bash
uv add "langgraph>=1.0.0" "langgraph-checkpoint-postgres>=2.0.0" "psycopg[binary]>=3.2" "psycopg-pool>=3.2" "structlog>=24.0" "langfuse>=3.0.0"
```

Expected: resolves and updates `pyproject.toml` + `uv.lock` without conflicts (langchain-core is already 1.x in this project, compatible with langgraph 1.x). If resolution fails, report the conflict — do not force pins.

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_config.py`:

```python
def test_database_url_psycopg_strips_asyncpg_driver():
    """AsyncPostgresSaver needs a plain postgresql:// URL (psycopg3), not the SQLAlchemy+asyncpg one."""
    from app.config import Settings

    s = Settings(
        openrouter_api_key="k",
        database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
        admin_api_key="a",
    )
    assert s.database_url_psycopg == "postgresql://user:pass@localhost:5432/db"


def test_langfuse_settings_default_to_disabled():
    """Without env vars, Langfuse keys default to empty strings (tracing disabled)."""
    from app.config import Settings

    s = Settings(
        openrouter_api_key="k",
        database_url="postgresql+asyncpg://u:p@h:5432/db",
        admin_api_key="a",
    )
    assert s.langfuse_public_key == ""
    assert s.langfuse_secret_key == ""
    assert s.langfuse_host == "https://cloud.langfuse.com"
```

Note: `Settings` reads `.env` if present — these tests pass explicit constructor kwargs for required fields, and the Langfuse fields must not be set in the test environment (`conftest.py` doesn't set them; a developer's local `backend/.env` might — if the second test fails locally because `.env` has keys, that's expected and fine in CI; guard it by monkeypatching if it becomes a problem: `monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)` and pass `_env_file=None` to `Settings(...)`).

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_config.py -v
```

Expected: the two new tests FAIL (`AttributeError: 'Settings' object has no attribute 'database_url_psycopg'` / missing `langfuse_public_key`).

- [ ] **Step 4: Implement settings**

In `backend/app/config.py`, inside `class Settings`, after the Twenty CRM block, add:

```python
    # Langfuse observability (optional — empty keys = tracing disabled)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
```

And after the `allowed_origins_list` property, add:

```python
    @property
    def database_url_psycopg(self) -> str:
        """Plain postgresql:// URL for psycopg3 (LangGraph checkpointer) — strips the SQLAlchemy asyncpg driver marker."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_config.py -v
```

Expected: all PASS.

- [ ] **Step 6: Update `.env.example`**

Append to `backend/.env.example`:

```bash
# Langfuse observability (optional — leave empty to disable tracing)
# Free account: https://cloud.langfuse.com -> project -> API keys
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

- [ ] **Step 7: Full unit suite + commit**

```bash
uv run python -m pytest -m "not integration"
git add pyproject.toml uv.lock app/config.py .env.example tests/test_config.py
git commit -m "feat: add langgraph/langfuse/structlog deps and settings"
```

Expected: 88 + 2 = 90 passed (3 integration deselected).

---

### Task 2: Structured logging module

**Files:**
- Create: `backend/app/logging_config.py`
- Test: `backend/tests/test_logging_config.py`

**Interfaces:**
- Produces: `configure_logging() -> None` (idempotent, safe to call multiple times) and the convention that request logs are emitted via `structlog.get_logger()` with event name `"chat_turn"`. Task 4 wires the actual log call into `/chat`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_logging_config.py`:

```python
import json

import structlog

from app.logging_config import configure_logging


def test_configure_logging_produces_json_lines(capsys):
    """After configure_logging(), a structlog event renders as a single JSON line
    with event name and bound key-value fields."""
    configure_logging()
    logger = structlog.get_logger()
    logger.info("chat_turn", session_id="abc-123", tokens_used=150, cost_usd=0.0001)

    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["event"] == "chat_turn"
    assert parsed["session_id"] == "abc-123"
    assert parsed["tokens_used"] == 150
    assert "timestamp" in parsed
    assert parsed["level"] == "info"


def test_configure_logging_is_idempotent():
    """Calling configure_logging() twice must not raise or duplicate handlers."""
    configure_logging()
    configure_logging()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run python -m pytest tests/test_logging_config.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.logging_config'`.

- [ ] **Step 3: Implement**

Create `backend/app/logging_config.py`:

```python
"""Structured JSON logging via structlog.

One JSON object per line to stdout — greppable locally, parseable by any
log aggregator later (Render, Grafana Loki, etc.). Call configure_logging()
once at app startup; it is idempotent.
"""

import logging

import structlog


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=False,  # keep False so tests/capsys see reconfiguration
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run python -m pytest tests/test_logging_config.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/logging_config.py tests/test_logging_config.py
git commit -m "feat: add structlog JSON logging configuration"
```

---

### Task 3: LangGraph agent module

**Files:**
- Create: `backend/app/agent.py`
- Test: `backend/tests/test_agent.py`

**Interfaces:**
- Consumes: `calculate_roi(**kwargs) -> dict` (`app.tools.roi`), `search_automation_patterns(task_description: str) -> dict` with keys `formatted`/`sources` (`app.tools.search`), `capture_lead(**kwargs) -> dict` (`mcp_server.server`), `calculate_cost(model, in, out) -> float` and `estimate_embedding_tokens(text) -> int` (`app.cost_tracker`), `get_settings()`.
- Produces (Task 4/6 rely on these exact names):
  - `AgentState` TypedDict with keys `messages`, `sources`, `tools_called`, `input_tokens`, `output_tokens`, `cost_usd`
  - `build_agent_graph(checkpointer) -> CompiledStateGraph`
  - `turn_input(message: str) -> dict` — the per-turn invoke input (resets per-turn channels)
  - `SYSTEM_PROMPT`, and the three tool schema constants moved here from `main.py` (`CALCULATE_ROI_TOOL`, `SEARCH_AUTOMATION_PATTERNS_TOOL`, `CAPTURE_LEAD_TOOL`)

**Design notes for the implementer (why it's built this way):**
- History persistence is the checkpointer's job. Per-turn channels (`sources`, `tools_called`, token/cost counters) use **default overwrite reducers** and are explicitly reset to zero/empty by `turn_input()` at the start of every turn — otherwise values checkpointed from the previous turn would leak into this turn's API response.
- Nodes that accumulate within a turn (tools node appending `tools_called`, agent node adding tokens) do so by reading current state and returning the updated value — works with overwrite reducers, survives multiple loop iterations within one turn.
- The system prompt is prepended at LLM-call time, **not** stored in `messages`, so it isn't duplicated in every checkpoint.
- The unknown-tool and broad `except` branches replicate `main.py`'s current behavior exactly (a malformed LLM tool call must not 500 — regression-tested bug).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_agent.py`:

```python
"""Unit tests for the LangGraph agent graph — LLM mocked, InMemorySaver checkpointer."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent import build_agent_graph, turn_input


def _make_ai_message(content=None, tool_calls=None, usage_metadata=None):
    """Minimal AIMessage stand-in: .content, .tool_calls, .usage_metadata.
    Uses a real AIMessage so add_messages/checkpointing serialization works."""
    from langchain_core.messages import AIMessage

    return AIMessage(
        content=content or "",
        tool_calls=tool_calls or [],
        usage_metadata=usage_metadata
        if usage_metadata is not None
        else {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    )


def _graph_and_config():
    graph = build_agent_graph(InMemorySaver())
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 10}
    return graph, config


def _patch_llm(side_effect):
    """Patch app.agent.ChatOpenAI so .bind_tools().ainvoke() yields side_effect in order."""
    mock_cls = patch("app.agent.ChatOpenAI").start()
    mock_cls.return_value.bind_tools.return_value.ainvoke = AsyncMock(side_effect=side_effect)
    return mock_cls


@pytest.fixture(autouse=True)
def _stop_patches():
    yield
    patch.stopall()


async def test_no_tool_calls_returns_reply_and_zero_sources():
    _patch_llm([_make_ai_message(content="Just tell me more about your process.")])
    graph, config = _graph_and_config()

    result = await graph.ainvoke(turn_input("hi"), config)

    assert result["messages"][-1].content == "Just tell me more about your process."
    assert result["sources"] == []
    assert result["tools_called"] == []
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 20


async def test_roi_tool_round_trip():
    tool_call = {
        "name": "calculate_roi",
        "args": {"hours_saved_per_week": 5, "hourly_rate": 30, "setup_cost": 1000},
        "id": "call_1",
    }
    _patch_llm([
        _make_ai_message(tool_calls=[tool_call]),
        _make_ai_message(content="You'd save 7800/year."),
    ])
    graph, config = _graph_and_config()

    result = await graph.ainvoke(turn_input("5h/week at 30/h, setup 1000"), config)

    assert result["messages"][-1].content == "You'd save 7800/year."
    assert result["tools_called"] == ["calculate_roi"]
    # tokens from both LLM calls accumulated
    assert result["input_tokens"] == 20
    assert result["output_tokens"] == 40


async def test_search_tool_populates_sources_and_embedding_tokens():
    fake_sources = [{"title": "Pattern A", "similarity": 0.9}]
    tool_call = {
        "name": "search_automation_patterns",
        "args": {"task_description": "x" * 40},
        "id": "call_2",
    }
    _patch_llm([
        _make_ai_message(tool_calls=[tool_call]),
        _make_ai_message(content="Based on Pattern A, use n8n."),
    ])
    graph, config = _graph_and_config()

    with patch(
        "app.agent.search_automation_patterns",
        new=AsyncMock(return_value={"formatted": "### Pattern A\n...", "sources": fake_sources}),
    ):
        result = await graph.ainvoke(turn_input("what to automate?"), config)

    assert result["sources"] == fake_sources
    # 10+10 LLM input + 40/4=10 estimated embedding tokens
    assert result["input_tokens"] == 30


async def test_multi_round_tool_loop():
    """The new capability vs the old 2-call loop: LLM can request tools twice in one turn."""
    call_a = {"name": "calculate_roi", "args": {"hours_saved_per_week": 2, "hourly_rate": 50, "setup_cost": 500}, "id": "a"}
    call_b = {"name": "calculate_roi", "args": {"hours_saved_per_week": 4, "hourly_rate": 50, "setup_cost": 500}, "id": "b"}
    _patch_llm([
        _make_ai_message(tool_calls=[call_a]),
        _make_ai_message(tool_calls=[call_b]),
        _make_ai_message(content="Comparing both scenarios: ..."),
    ])
    graph, config = _graph_and_config()

    result = await graph.ainvoke(turn_input("compare 2h vs 4h saved"), config)

    assert result["messages"][-1].content == "Comparing both scenarios: ..."
    assert result["tools_called"] == ["calculate_roi", "calculate_roi"]


async def test_malformed_tool_args_do_not_crash():
    """Missing/None required arg from the LLM -> error fed back as ToolMessage, not an exception."""
    bad_call = {
        "name": "calculate_roi",
        "args": {"hours_saved_per_week": 3, "hourly_rate": None, "setup_cost": 200},
        "id": "bad",
    }
    _patch_llm([
        _make_ai_message(tool_calls=[bad_call]),
        _make_ai_message(content="Could you share your hourly rate?"),
    ])
    graph, config = _graph_and_config()

    result = await graph.ainvoke(turn_input("3h a week on posts"), config)

    assert result["messages"][-1].content == "Could you share your hourly rate?"


async def test_unknown_tool_returns_error_result():
    ghost_call = {"name": "ghost_tool", "args": {}, "id": "g"}
    _patch_llm([
        _make_ai_message(tool_calls=[ghost_call]),
        _make_ai_message(content="Sorry, let me try again."),
    ])
    graph, config = _graph_and_config()

    result = await graph.ainvoke(turn_input("hi"), config)

    tool_msgs = [m for m in result["messages"] if m.type == "tool"]
    assert "Unknown tool" in tool_msgs[-1].content


async def test_second_turn_sees_first_turn_history_and_resets_counters():
    """Checkpointer memory: turn 2 on the same thread_id includes turn 1 messages,
    but per-turn counters reset."""
    llm_mock = _patch_llm([
        _make_ai_message(content="First reply."),
        _make_ai_message(content="Second reply."),
    ])
    graph, config = _graph_and_config()

    r1 = await graph.ainvoke(turn_input("first question"), config)
    r2 = await graph.ainvoke(turn_input("second question"), config)

    # per-turn counters reset between turns (not cumulative across the session)
    assert r1["input_tokens"] == 10 and r2["input_tokens"] == 10
    # the second LLM call received the full prior history (system + h1 + a1 + h2 = 4 messages)
    second_call_messages = llm_mock.return_value.bind_tools.return_value.ainvoke.call_args_list[1].args[0]
    contents = [m.content for m in second_call_messages]
    assert "first question" in contents
    assert "First reply." in contents
    assert "second question" in contents


async def test_capture_lead_dispatched():
    lead_call = {
        "name": "capture_lead",
        "args": {"name": "Jane", "email": "j@x.com", "company": "X", "pain_point": "invoicing"},
        "id": "l",
    }
    _patch_llm([
        _make_ai_message(tool_calls=[lead_call]),
        _make_ai_message(content="Saved, thanks Jane."),
    ])
    graph, config = _graph_and_config()

    with patch("app.agent.capture_lead", new=AsyncMock(return_value={"status": "created", "person_id": "1"})) as lead_mock:
        result = await graph.ainvoke(turn_input("Jane, j@x.com, X, invoicing"), config)

    lead_mock.assert_awaited_once_with(name="Jane", email="j@x.com", company="X", pain_point="invoicing")
    assert result["tools_called"] == ["capture_lead"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_agent.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent'`.

- [ ] **Step 3: Implement `app/agent.py`**

Create `backend/app/agent.py`. The three tool schema dicts and `SYSTEM_PROMPT` are **moved verbatim from `main.py`** (lines 80–151 of the current file) — copy them exactly; they are elided here only to avoid drift, everything else is complete:

```python
"""LangGraph agent: explicit StateGraph replacing main.py's bounded 2-call loop.

Graph shape:  START -> agent -> (tool_calls? tools : END),  tools -> agent
History is owned by the checkpointer (thread_id = session_id). Per-turn
channels (sources, tools_called, token/cost counters) use overwrite reducers
and are reset by turn_input() each turn so checkpointed values from the
previous turn never leak into this turn's API response.
"""

import json
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.config import get_settings
from app.cost_tracker import calculate_cost, estimate_embedding_tokens
from app.tools.roi import calculate_roi
from app.tools.search import search_automation_patterns
from mcp_server.server import capture_lead

# --- Tool schemas + system prompt: MOVED VERBATIM from app/main.py ---
CALCULATE_ROI_TOOL = { ... }              # copy exactly from main.py
SEARCH_AUTOMATION_PATTERNS_TOOL = { ... } # copy exactly from main.py
CAPTURE_LEAD_TOOL = { ... }               # copy exactly from main.py
SYSTEM_PROMPT = ( ... )                   # copy exactly from main.py
# ---------------------------------------------------------------------


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    # Per-turn channels (overwrite reducer, reset by turn_input each turn):
    sources: list[dict]
    tools_called: list[str]
    input_tokens: int
    output_tokens: int
    cost_usd: float


def turn_input(message: str) -> dict:
    """Invoke input for one user turn — appends the message, resets per-turn channels."""
    return {
        "messages": [HumanMessage(content=message)],
        "sources": [],
        "tools_called": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }


def _usage_tokens(ai_message) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from an AIMessage's usage_metadata, defaulting to 0."""
    usage = getattr(ai_message, "usage_metadata", None) or {}
    return usage.get("input_tokens", 0), usage.get("output_tokens", 0)


async def agent_node(state: AgentState) -> dict:
    """One LLM call. System prompt prepended at call time (not checkpointed)."""
    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        max_tokens=settings.max_output_tokens,
    ).bind_tools([CALCULATE_ROI_TOOL, SEARCH_AUTOMATION_PATTERNS_TOOL, CAPTURE_LEAD_TOOL])

    ai_message = await llm.ainvoke([SystemMessage(content=SYSTEM_PROMPT)] + state["messages"])
    in_tok, out_tok = _usage_tokens(ai_message)
    return {
        "messages": [ai_message],
        "input_tokens": state["input_tokens"] + in_tok,
        "output_tokens": state["output_tokens"] + out_tok,
        "cost_usd": state["cost_usd"] + calculate_cost(settings.chat_model, in_tok, out_tok),
    }


async def tools_node(state: AgentState) -> dict:
    """Execute every tool call from the last AIMessage. Tool errors are fed back
    as ToolMessages so the LLM can recover — never raised (a malformed LLM tool
    call must not 500 the request; real bug, regression-tested)."""
    settings = get_settings()
    last = state["messages"][-1]
    tool_messages: list[ToolMessage] = []
    sources = state["sources"]
    tools_called = list(state["tools_called"])
    input_tokens = state["input_tokens"]
    cost_usd = state["cost_usd"]

    for tool_call in last.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tools_called.append(tool_name)
        try:
            if tool_name == "calculate_roi":
                tool_result = calculate_roi(**tool_args)
            elif tool_name == "search_automation_patterns":
                search_result = await search_automation_patterns(**tool_args)
                sources = search_result["sources"]
                tool_result = search_result["formatted"]
                embedding_tokens = estimate_embedding_tokens(tool_args.get("task_description", ""))
                input_tokens += embedding_tokens
                cost_usd += calculate_cost(settings.embedding_model, embedding_tokens, 0)
            elif tool_name == "capture_lead":
                tool_result = await capture_lead(**tool_args)
            else:
                tool_result = {"status": "error", "detail": f"Unknown tool {tool_name}"}
        except Exception as e:
            tool_result = {"status": "error", "detail": f"{tool_name} failed: {e}"}

        tool_messages.append(
            ToolMessage(content=json.dumps(tool_result), tool_call_id=tool_call["id"])
        )

    return {
        "messages": tool_messages,
        "sources": sources,
        "tools_called": tools_called,
        "input_tokens": input_tokens,
        "cost_usd": cost_usd,
    }


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def build_agent_graph(checkpointer):
    """Compile the agent graph. Checkpointer is injected: AsyncPostgresSaver in
    production (main.py lifespan), InMemorySaver in tests."""
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_agent.py -v
```

Expected: 8 PASS. If `test_second_turn_sees_first_turn_history_and_resets_counters` fails on the message-content assertion, print `second_call_messages` — the system message is index 0; user/assistant history follows.

- [ ] **Step 5: Full unit suite + commit**

```bash
uv run python -m pytest -m "not integration"
git add app/agent.py tests/test_agent.py
git commit -m "feat: add LangGraph agent graph (explicit StateGraph, checkpointer-owned history)"
```

Expected: all pass — `main.py` is untouched so the 15 existing chat tests still pass.

---

### Task 4: Rewire `/chat` onto the graph (lifespan, Postgres checkpointer, structured log)

**Files:**
- Modify: `backend/app/main.py` (remove tool schemas/SYSTEM_PROMPT/`_usage_tokens`/manual loop; add lifespan + graph invoke + `chat_turn` log)
- Modify: `backend/tests/test_chat.py` (rewrite mocks for the graph path; keep the same behavioral coverage)

**Interfaces:**
- Consumes: `build_agent_graph`, `turn_input`, `AgentState` keys (Task 3); `configure_logging` (Task 2); `Settings.database_url_psycopg` (Task 1).
- Produces: `app.state.agent_graph` (compiled graph, set by lifespan in prod, by test fixture in tests); helpers `_get_or_create_turn_count(session_id) -> int` and `_increment_turn(session_id) -> None` (Task 6 reuses both, plus the same pre-check block).

- [ ] **Step 1: Rewrite `tests/test_chat.py`**

Replace the file's helper section and adjust every test. Full new helper section (test bodies keep their current names/assertions, changed only as noted):

```python
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from app.agent import build_agent_graph
from app.config import get_settings
from app.main import app


def _mock_db_session(row=None):
    """Mock AsyncSessionLocal context manager; execute() -> result with .fetchone() -> row."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.fetchone.return_value = row
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    return mock_session


def _make_ai_message(content=None, tool_calls=None, usage_metadata=None):
    from langchain_core.messages import AIMessage

    return AIMessage(
        content=content or "",
        tool_calls=tool_calls or [],
        usage_metadata=usage_metadata
        if usage_metadata is not None
        else {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    )


def _configure_mock_llm(mock_chat_openai_cls, ainvoke_side_effect):
    """The LLM now lives in app.agent, not app.main."""
    mock_chat_openai_cls.return_value.bind_tools.return_value.ainvoke = AsyncMock(
        side_effect=ainvoke_side_effect
    )


@pytest.fixture(autouse=True)
def memory_graph():
    """Fresh in-memory-checkpointed graph per test. ASGITransport never runs the
    lifespan, so tests must set app.state.agent_graph themselves."""
    app.state.agent_graph = build_agent_graph(InMemorySaver())
    yield app.state.agent_graph


async def _post_chat(payload):
    with patch("app.main.check_ip_rate_limit", new=AsyncMock()):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post("/chat", json=payload)
```

Per-test changes (mechanical, same behavioral assertions):
- Every `patch("app.main.ChatOpenAI")` becomes `patch("app.agent.ChatOpenAI")`.
- Every `patch("app.main.search_automation_patterns", ...)` becomes `patch("app.agent.search_automation_patterns", ...)`.
- Every `patch("app.main.capture_lead", ...)` becomes `patch("app.agent.capture_lead", ...)`.
- `MagicMock(turn_count=N, history=[])` rows become `MagicMock(turn_count=N)` (history is no longer read).
- `test_chat_calculate_roi_tool_called_and_result_fed_back`'s final assertion `...ainvoke.call_count == 2` stays valid (agent node → tools → agent node = 2 LLM calls).
- `test_chat_empty_llm_content_falls_back_to_default_reply`: unchanged semantics — the endpoint's fallback string applies when the final content is empty.
- All other tests keep their exact assertions (`session_id` UUID, `turns_remaining` arithmetic, 422/429 paths, sources population, token/cost arithmetic including the embedding estimate).

Add one new test at the end:

```python
@pytest.mark.asyncio
async def test_chat_emits_structured_chat_turn_log():
    """Every successful /chat emits one structured chat_turn event with the key request facts."""
    import structlog

    mock_session = _mock_db_session(row=None)
    ai_msg = _make_ai_message(content="Advice here.")

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.agent.ChatOpenAI") as mock_chat_cls,
        structlog.testing.capture_logs() as logs,
    ):
        _configure_mock_llm(mock_chat_cls, [ai_msg])
        response = await _post_chat({"message": "How do I automate invoicing?"})

    assert response.status_code == 200
    chat_logs = [entry for entry in logs if entry["event"] == "chat_turn"]
    assert len(chat_logs) == 1
    entry = chat_logs[0]
    assert entry["session_id"] == response.json()["session_id"]
    assert entry["tools_called"] == []
    assert entry["tokens_used"] == 30
    assert "latency_ms" in entry
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_chat.py -v
```

Expected: FAIL — `main.py` still runs the manual loop with its own `ChatOpenAI`, so the `app.agent.ChatOpenAI` patches never take effect and real network calls are attempted / mocks are unused.

- [ ] **Step 3: Rewrite `app/main.py`**

Full replacement of the file's chat-related parts. New `main.py` top-to-bottom structure (health + admin_ingest endpoints stay exactly as they are):

```python
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

import structlog
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphRecursionError
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from sqlalchemy import text

from app.agent import build_agent_graph, turn_input
from app.config import get_settings
from app.db import AsyncSessionLocal
from app.logging_config import configure_logging
from app.rag.ingest import ingest_static_kb
from app.rag.live_ingester import ingest_live_docs
from app.rate_limit import check_ip_rate_limit, get_client_ip

logger = structlog.get_logger()

FALLBACK_REPLY = "Could you tell me more about what you're looking to automate?"
RECURSION_FALLBACK_REPLY = (
    "That one took more steps than I allow myself — could you rephrase or split the question?"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: logging + LangGraph Postgres checkpointer + compiled agent graph.
    ASGITransport in unit tests never runs this — tests set app.state.agent_graph directly."""
    configure_logging()
    settings = get_settings()
    pool = AsyncConnectionPool(
        conninfo=settings.database_url_psycopg,
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()  # idempotent — creates checkpoint tables on first run
    app.state.agent_graph = build_agent_graph(checkpointer)
    logger.info("startup_complete", checkpointer="AsyncPostgresSaver")
    yield
    await pool.close()


app = FastAPI(
    title="Automate This API",
    description="SMB automation advisor — AI-powered consulting chatbot backend",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS middleware block: unchanged from current file.
# /health endpoint: unchanged. /admin/ingest endpoint: unchanged.
# ChatRequest / ChatResponse models: unchanged.


async def _get_or_create_turn_count(session_id: str) -> int:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text("SELECT turn_count FROM conversations WHERE session_id = :sid"),
                {"sid": session_id},
            )
        ).fetchone()
        if row is None:
            await session.execute(
                text("INSERT INTO conversations (session_id, turn_count, history) VALUES (:sid, 0, '[]')"),
                {"sid": session_id},
            )
            await session.commit()
            return 0
        return row.turn_count


async def _increment_turn(session_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE conversations SET turn_count = turn_count + 1, "
                "updated_at = NOW() WHERE session_id = :sid"
            ),
            {"sid": session_id},
        )
        await session.commit()


@app.post("/chat", tags=["chat"])
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Multi-turn chat via the LangGraph agent. History lives in LangGraph
    checkpoints (thread_id = session_id); conversations table only counts turns."""
    settings = get_settings()

    if len(body.message) > settings.max_input_chars:
        raise HTTPException(422, f"Message exceeds {settings.max_input_chars} chars")

    await check_ip_rate_limit(get_client_ip(request))

    session_id = body.session_id or str(uuid.uuid4())
    turn_count = await _get_or_create_turn_count(session_id)
    if turn_count >= settings.max_turns_per_session:
        raise HTTPException(429, "Session turn limit reached")

    graph = app.state.agent_graph
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 10,
        "metadata": {"langfuse_session_id": session_id},
    }

    started = time.perf_counter()
    try:
        result = await graph.ainvoke(turn_input(body.message), config)
        reply = result["messages"][-1].content or FALLBACK_REPLY
    except GraphRecursionError:
        result = {"sources": [], "tools_called": [], "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        reply = RECURSION_FALLBACK_REPLY
    latency_ms = round((time.perf_counter() - started) * 1000)

    await _increment_turn(session_id)

    tokens_used = result["input_tokens"] + result["output_tokens"]
    turns_remaining = settings.max_turns_per_session - (turn_count + 1)
    logger.info(
        "chat_turn",
        session_id=session_id,
        tools_called=result["tools_called"],
        tokens_used=tokens_used,
        cost_usd=round(result["cost_usd"], 6),
        latency_ms=latency_ms,
        turns_remaining=turns_remaining,
    )

    return ChatResponse(
        session_id=session_id,
        reply=reply,
        sources=result["sources"],
        turns_remaining=turns_remaining,
        tokens_used=tokens_used,
        cost_usd=round(result["cost_usd"], 6),
    )
```

Deletions from the old file: the three tool schema dicts, `SYSTEM_PROMPT`, `_usage_tokens`, all LangChain message imports, `ChatOpenAI` import, `json` import (no longer needed in main), the `calculate_roi`/`search_automation_patterns`/`capture_lead`/`cost_tracker` imports (now only used in `app/agent.py`).

Note: `config["metadata"]["langfuse_session_id"]` is inert until Task 5 adds the callback handler — harmless to include now so Task 5's diff stays minimal.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_chat.py tests/test_agent.py -v
```

Expected: all PASS (old count 15 → 16 chat tests incl. the new log test, plus 8 agent tests).

- [ ] **Step 5: Full unit suite + commit**

```bash
uv run python -m pytest -m "not integration"
git add app/main.py tests/test_chat.py
git commit -m "feat: wire /chat onto LangGraph agent with Postgres checkpointer and structured chat_turn logging"
```

---

### Task 5: Langfuse observability (env-gated)

**Files:**
- Create: `backend/app/observability.py`
- Modify: `backend/app/main.py` (add callbacks to the graph config)
- Test: `backend/tests/test_observability.py`

**Interfaces:**
- Consumes: `Settings.langfuse_*` fields (Task 1).
- Produces: `get_langfuse_callbacks() -> list` — `[]` when keys are unset, `[CallbackHandler]` when set. Task 6 reuses it.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_observability.py`:

```python
from unittest.mock import MagicMock, patch

from app import observability
from app.observability import get_langfuse_callbacks


def _settings_mock(public="", secret=""):
    s = MagicMock()
    s.langfuse_public_key = public
    s.langfuse_secret_key = secret
    s.langfuse_host = "https://cloud.langfuse.com"
    return s


def setup_function():
    observability._init_langfuse.cache_clear()


def test_no_keys_returns_empty_callbacks():
    with patch("app.observability.get_settings", return_value=_settings_mock()):
        assert get_langfuse_callbacks() == []


def test_keys_present_returns_callback_handler():
    with (
        patch("app.observability.get_settings", return_value=_settings_mock("pk", "sk")),
        patch("langfuse.Langfuse") as langfuse_cls,
        patch("langfuse.langchain.CallbackHandler") as handler_cls,
    ):
        callbacks = get_langfuse_callbacks()

    langfuse_cls.assert_called_once_with(
        public_key="pk", secret_key="sk", host="https://cloud.langfuse.com"
    )
    assert callbacks == [handler_cls.return_value]


def test_langfuse_client_initialised_once():
    with (
        patch("app.observability.get_settings", return_value=_settings_mock("pk", "sk")),
        patch("langfuse.Langfuse") as langfuse_cls,
        patch("langfuse.langchain.CallbackHandler"),
    ):
        get_langfuse_callbacks()
        get_langfuse_callbacks()

    langfuse_cls.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_observability.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.observability'`.

- [ ] **Step 3: Implement**

Create `backend/app/observability.py`:

```python
"""Langfuse tracing — env-gated. No keys in settings -> everything is a no-op.

Langfuse v3 SDK: constructing Langfuse(...) once registers the singleton
client; CallbackHandler() then picks it up. Keys come from get_settings()
(pydantic .env), NOT os.environ — the SDK's own env-var lookup would miss
them because pydantic-settings never exports to os.environ.
"""

from functools import lru_cache

from app.config import get_settings


@lru_cache(maxsize=1)
def _init_langfuse():
    """Initialise the Langfuse client singleton once, or None when disabled."""
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    import langfuse

    return langfuse.Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )


def get_langfuse_callbacks() -> list:
    """LangChain callbacks for graph config — [CallbackHandler] or [] when disabled."""
    if _init_langfuse() is None:
        return []
    import langfuse.langchain

    return [langfuse.langchain.CallbackHandler()]
```

API-drift check for the implementer: verify the import path with `uv run python -c "from langfuse.langchain import CallbackHandler; print('ok')"`. If the installed langfuse version moved it (v2 used `langfuse.callback.CallbackHandler`), adapt the import in **both** module and test, and note it in your report.

- [ ] **Step 4: Wire into `/chat`**

In `app/main.py`: add `from app.observability import get_langfuse_callbacks` and extend the graph config in `chat`:

```python
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 10,
        "callbacks": get_langfuse_callbacks(),
        "metadata": {"langfuse_session_id": session_id},
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_observability.py tests/test_chat.py -v
```

Expected: all PASS (chat tests unaffected — no Langfuse keys in the test env, so callbacks is `[]`).

- [ ] **Step 6: Commit**

```bash
git add app/observability.py app/main.py tests/test_observability.py
git commit -m "feat: add env-gated Langfuse tracing to the agent graph"
```

---

### Task 6: SSE streaming endpoint `/chat/stream`

**Files:**
- Modify: `backend/app/main.py` (add endpoint)
- Test: `backend/tests/test_chat_stream.py`

**Interfaces:**
- Consumes: `turn_input`, `app.state.agent_graph`, `_get_or_create_turn_count`, `_increment_turn`, `get_langfuse_callbacks`, `FALLBACK_REPLY`.
- Produces: `POST /chat/stream` — same request body as `/chat`; response `text/event-stream` with events:
  - `event: token` / `data: {"content": "<text chunk>"}` (zero or more)
  - `event: done` / `data: <full ChatResponse JSON>` (exactly one, on success)
  - `event: error` / `data: {"detail": "..."}` (on mid-stream failure)
  Pre-stream failures (422/429) are plain HTTP errors — they happen before the stream starts. This contract is what the Next.js frontend (Phase 2 plan) will consume.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_chat_stream.py`:

```python
"""SSE streaming endpoint tests. Uses GenericFakeChatModel (a real BaseChatModel
that streams content word-by-word) so LangGraph's stream_mode='messages' has
real token callbacks to surface — a MagicMock LLM can't emit those."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agent import build_agent_graph
from app.config import get_settings
from app.main import app


def _mock_db_session(row=None):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.fetchone.return_value = row
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    return mock_session


@pytest.fixture(autouse=True)
def memory_graph():
    app.state.agent_graph = build_agent_graph(InMemorySaver())
    yield app.state.agent_graph


def _fake_streaming_llm(reply_text):
    """A real chat model that streams: patched in as the bind_tools() result."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    return GenericFakeChatModel(messages=iter([AIMessage(content=reply_text)]))


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.strip().split("\n\n"):
        lines = block.strip().split("\n")
        event = next(l.removeprefix("event: ") for l in lines if l.startswith("event: "))
        data = next(l.removeprefix("data: ") for l in lines if l.startswith("data: "))
        events.append((event, json.loads(data)))
    return events


async def _post_stream(payload):
    with patch("app.main.check_ip_rate_limit", new=AsyncMock()):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post("/chat/stream", json=payload)


async def test_stream_emits_tokens_then_done_with_full_metadata():
    mock_session = _mock_db_session(row=None)
    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.agent.ChatOpenAI") as mock_chat_cls,
    ):
        mock_chat_cls.return_value.bind_tools.return_value = _fake_streaming_llm(
            "Automate your invoicing with n8n."
        )
        response = await _post_stream({"message": "help me automate invoicing"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)

    done_events = [d for e, d in events if e == "done"]
    assert len(done_events) == 1
    done = done_events[0]
    assert done["reply"] == "Automate your invoicing with n8n."
    uuid.UUID(done["session_id"])
    assert done["turns_remaining"] == get_settings().max_turns_per_session - 1
    assert done["sources"] == []

    # Whether tokens arrive word-by-word or as one chunk, their concatenation is the reply.
    token_text = "".join(d["content"] for e, d in events if e == "token")
    assert token_text.replace(" ", "") == done["reply"].replace(" ", "")


async def test_stream_message_too_long_is_plain_422():
    settings = get_settings()
    response = await _post_stream({"message": "x" * (settings.max_input_chars + 1)})
    assert response.status_code == 422


async def test_stream_turn_limit_is_plain_429():
    settings = get_settings()
    row = MagicMock(turn_count=settings.max_turns_per_session)
    with patch("app.main.AsyncSessionLocal", return_value=_mock_db_session(row=row)):
        response = await _post_stream(
            {"session_id": str(uuid.uuid4()), "message": "one more"}
        )
    assert response.status_code == 429


async def test_stream_mid_stream_failure_emits_error_event():
    """Failures after the stream starts can't become HTTP errors — they must
    surface as an SSE error event, never a hung/truncated stream."""
    mock_session = _mock_db_session(row=None)
    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.agent.ChatOpenAI") as mock_chat_cls,
    ):
        mock_chat_cls.return_value.bind_tools.return_value.astream = MagicMock(
            side_effect=RuntimeError("boom")
        )
        mock_chat_cls.return_value.bind_tools.return_value.ainvoke = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        response = await _post_stream({"message": "hello"})

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1][0] == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_chat_stream.py -v
```

Expected: FAIL with 404 (`/chat/stream` doesn't exist), so `_parse_sse`/status asserts fail.

- [ ] **Step 3: Implement the endpoint**

In `app/main.py`, add imports `import json` and `from fastapi.responses import StreamingResponse`, then after the `chat` endpoint:

```python
@app.post("/chat/stream", tags=["chat"])
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    """SSE variant of /chat for the Next.js frontend: streams LLM tokens as
    `token` events, then one `done` event with the full ChatResponse payload.
    All request-rejection paths (422/429) fire BEFORE streaming starts, as
    plain HTTP errors — once the stream is open only SSE events come back."""
    settings = get_settings()

    if len(body.message) > settings.max_input_chars:
        raise HTTPException(422, f"Message exceeds {settings.max_input_chars} chars")

    await check_ip_rate_limit(get_client_ip(request))

    session_id = body.session_id or str(uuid.uuid4())
    turn_count = await _get_or_create_turn_count(session_id)
    if turn_count >= settings.max_turns_per_session:
        raise HTTPException(429, "Session turn limit reached")

    graph = app.state.agent_graph
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 10,
        "callbacks": get_langfuse_callbacks(),
        "metadata": {"langfuse_session_id": session_id},
    }

    async def event_stream():
        started = time.perf_counter()
        try:
            async for chunk, metadata in graph.astream(
                turn_input(body.message), config, stream_mode="messages"
            ):
                # Only surface assistant text (agent node); tool results and
                # tool-call argument deltas are not user-facing tokens.
                if metadata.get("langgraph_node") == "agent" and isinstance(chunk.content, str) and chunk.content:
                    yield f"event: token\ndata: {json.dumps({'content': chunk.content})}\n\n"

            state = await graph.aget_state(config)
            values = state.values
            reply = values["messages"][-1].content or FALLBACK_REPLY
            await _increment_turn(session_id)

            tokens_used = values["input_tokens"] + values["output_tokens"]
            turns_remaining = settings.max_turns_per_session - (turn_count + 1)
            logger.info(
                "chat_turn",
                session_id=session_id,
                tools_called=values["tools_called"],
                tokens_used=tokens_used,
                cost_usd=round(values["cost_usd"], 6),
                latency_ms=round((time.perf_counter() - started) * 1000),
                turns_remaining=turns_remaining,
                streamed=True,
            )
            payload = ChatResponse(
                session_id=session_id,
                reply=reply,
                sources=values["sources"],
                turns_remaining=turns_remaining,
                tokens_used=tokens_used,
                cost_usd=round(values["cost_usd"], 6),
            ).model_dump()
            yield f"event: done\ndata: {json.dumps(payload)}\n\n"
        except Exception as e:  # noqa: BLE001 — stream is already open; must emit SSE, not raise
            logger.error("chat_stream_error", session_id=session_id, error=str(e))
            yield f"event: error\ndata: {json.dumps({'detail': 'The advisor hit a snag mid-reply — please retry.'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_chat_stream.py -v
```

Expected: 4 PASS. If `test_stream_emits_tokens_then_done_with_full_metadata` gets zero token events AND a correct done event, LangGraph isn't propagating token callbacks from the fake model — check that `GenericFakeChatModel` is being returned from `bind_tools` (not wrapped in a MagicMock), and that the metadata filter key is `langgraph_node` (print `metadata` from the loop to verify the key name for the installed langgraph version).

- [ ] **Step 5: Full unit suite + commit**

```bash
uv run python -m pytest -m "not integration"
git add app/main.py tests/test_chat_stream.py
git commit -m "feat: add SSE streaming endpoint /chat/stream for the future Next.js frontend"
```

---

### Task 7: Docs, full verification, live smoke test

**Files:**
- Modify: `README.md` (repo root)
- Modify: `backend/.env.example` (only if Task 1 missed anything)

**Interfaces:** none — verification and documentation task.

- [ ] **Step 1: Full test suite**

```bash
uv run python -m pytest -m "not integration" -v
```

Expected: ~105 passed (90 from Task 1 baseline + 2 logging + 8 agent + 1 new chat log test + 3 observability + 4 stream − adjustments), 0 failed. Record the exact count.

- [ ] **Step 2: Integration tests against live Postgres**

From repo root: `docker compose up -d`, then:

```bash
uv run python -m pytest -m integration -v
```

Expected: 3 passed.

- [ ] **Step 3: Live smoke test (real OpenRouter, real Postgres)**

```bash
uv run python -m uvicorn app.main:app --port 8000
```

(Use another port if 8000 is occupied — Twenty CRM sometimes holds it.) Verify on startup: the `startup_complete` JSON log line appears, and `AsyncPostgresSaver.setup()` created its tables (`psql`: `\dt checkpoint*` shows `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`).

Then:
1. `POST /chat` `{"message": "I spend 4 hours a week manually creating invoices"}` → 200, non-empty reply, `sources` populated (search tool fires), a `chat_turn` JSON log line with `tools_called`, non-zero `tokens_used`/`cost_usd`.
2. Second `POST /chat` with the returned `session_id`, message `"what did I just say I spend time on?"` → the reply must reference invoicing — **proves checkpointer memory works across turns with no JSONB history**.
3. `POST /chat/stream` with `curl -N` → `token` events followed by one `done` event.
4. If the user has added Langfuse keys to `.env`: confirm a trace appears at cloud.langfuse.com with the session id in metadata. If keys are absent, confirm the server runs cleanly with tracing disabled (no errors/warnings).
5. Stop the server after testing.

- [ ] **Step 4: Update README**

Update these README sections to reflect reality (keep the existing honest tone):
- **Stack table:** LLM orchestration row → "LangGraph (explicit StateGraph agent) + langchain-openai `ChatOpenAI`; multi-step tool loop with recursion limit"; add rows: "Observability | Langfuse (env-gated) + structlog JSON logs"; "Memory | LangGraph AsyncPostgresSaver checkpoints (thread_id = session_id)".
- **What It Does:** add streaming endpoint bullet.
- **API Endpoints table:** add `POST /chat/stream` row with the SSE event contract.
- **Sprint 2 coverage table:** flip "Logging and monitoring" from ⚠️ Partial to ✅ (structlog `chat_turn` events + Langfuse traces).
- **Database Schema:** note that `conversations.history` is no longer written (kept for backward compat; LangGraph checkpoint tables `checkpoints`/`checkpoint_blobs`/`checkpoint_writes` are auto-created by `AsyncPostgresSaver.setup()` at startup) and that existing pre-LangGraph sessions lose their in-flight history (dev-only, acceptable).
- **Roadmap / Known Gaps:** remove the structured-logging gap; add "Sprint 3: long-term memory, `suggest_tool_stack` tool, Next.js frontend (Phase 2 plan)".
- Add a short "Observability" section: how to set Langfuse keys, what a `chat_turn` log line looks like.

- [ ] **Step 5: Commit + push**

```bash
git add README.md .env.example
git commit -m "docs: document LangGraph agent, Langfuse observability, structured logging, and /chat/stream"
git push origin main
```

---

## Self-review notes (done at plan time)

- **Spec coverage:** logging → Task 2+4; Langfuse → Task 5; LangGraph → Tasks 3+4; streaming (needed before Next.js) → Task 6; Next.js itself is explicitly deferred to a Phase 2 plan.
- **Type consistency:** `AgentState` keys (`sources`, `tools_called`, `input_tokens`, `output_tokens`, `cost_usd`) match between Task 3 (definition), Task 4 (`result[...]` access), and Task 6 (`values[...]` access). `turn_input`, `build_agent_graph`, `_get_or_create_turn_count`, `_increment_turn`, `get_langfuse_callbacks` names consistent across tasks.
- **Known API-drift risks flagged inline:** langfuse `CallbackHandler` import path (Task 5 Step 3), langgraph `stream_mode="messages"` metadata key name (Task 6 Step 4). Both have verification instructions.
