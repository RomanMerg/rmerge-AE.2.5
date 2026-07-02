# Token/Cost Tracking + Per-IP Rate Limiting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the Sprint 2 "token/cost display" Medium-optional gap and close the session-cycling bypass in `/chat`'s rate limiting by adding a Postgres-backed per-IP request limit.

**Architecture:** Two small, independent modules (`app/rate_limit.py`, `app/cost_tracker.py`) built and unit-tested standalone, then wired into `/chat` in one integration task. `ChatResponse` gains `tokens_used`/`cost_usd`. The Gradio demo displays both plus a clearer message on either kind of rate limit.

**Tech Stack:** Same as the rest of the project — FastAPI, SQLAlchemy async + raw `text()` SQL, no new dependencies.

## Global Constraints

- Package manager: `uv` only.
- **Known issue in this checkout:** `uv run pytest` fails with `error: uv trampoline failed to canonicalize script path`. Use `uv run python -m pytest ...` instead.
- Settings: always `get_settings()`.
- Async everywhere for DB calls; raw `text()` SQL, no ORM models.
- No new in-memory state — rate-limit state is Postgres-backed, matching how `turn_count`/`history` already persist.
- Comments only when WHY is non-obvious.
- Full spec: `docs/superpowers/specs/2026-07-02-cost-tracking-rate-limiting-design.md`.

---

## File Map

| File | Responsibility |
|---|---|
| `init.sql` | New `chat_requests` table + index |
| `backend/app/config.py` | New `max_requests_per_ip_per_hour` setting |
| `backend/app/rate_limit.py` | `get_client_ip()`, `check_ip_rate_limit()` |
| `backend/app/cost_tracker.py` | `MODEL_PRICING`, `calculate_cost()`, `estimate_embedding_tokens()` |
| `backend/app/main.py` | Wire both into `/chat`; add `tokens_used`/`cost_usd` to `ChatResponse` |
| `frontend/app.py` | Show tokens/cost per turn; clearer 429 messaging |

---

## Task 1: Per-IP rate limiting

**Files:**
- Modify: `init.sql` (append new table)
- Modify: `backend/app/config.py`
- Create: `backend/app/rate_limit.py`
- Create: `backend/tests/test_rate_limit.py`

**Interfaces:**
- Produces: `get_client_ip(request: fastapi.Request) -> str`; `async check_ip_rate_limit(ip_address: str) -> None` (raises `fastapi.HTTPException(429, ...)` when the IP has made `>= settings.max_requests_per_ip_per_hour` requests in the last hour, otherwise records the request and returns `None`).

### Context

`max_turns_per_session` only limits turns *within* one `session_id`, which is client-supplied and trivially regenerated — a script can bypass it entirely by starting a fresh session on every call. This task adds an independent, Postgres-backed per-IP limit, checked before any other `/chat` work.

- [ ] **Step 1: Append the new table to `init.sql`**

Add to the end of `init.sql` (after the existing `conversations_updated_at` trigger):

```sql

-- Per-IP request logging for rate limiting on POST /chat
CREATE TABLE IF NOT EXISTS chat_requests (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ip_address TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_requests_ip_created_idx
    ON chat_requests (ip_address, created_at);
```

**Important — this does NOT auto-apply to an already-running Postgres container.** `init.sql` only runs on a fresh volume (`docker-entrypoint-initdb.d`). Anyone testing against an existing database (including `twenty-db-1`, used throughout this project's local dev) must run the `CREATE TABLE`/`CREATE INDEX` statements above manually against that database before this feature will work. Note this in the task report.

- [ ] **Step 2: Add the new setting**

In `backend/app/config.py`, add after the `# Twenty CRM` block:

```python
    # Anti-abuse: per-IP rate limiting on /chat
    max_requests_per_ip_per_hour: int = 30
```

- [ ] **Step 3: Write the failing tests**

Create `backend/tests/test_rate_limit.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _mock_request(headers=None, client_host="1.2.3.4"):
    request = MagicMock()
    request.headers = headers or {}
    request.client.host = client_host
    return request


def test_get_client_ip_prefers_x_forwarded_for():
    from app.rate_limit import get_client_ip

    request = _mock_request(headers={"X-Forwarded-For": "5.6.7.8, 9.10.11.12"}, client_host="1.2.3.4")
    assert get_client_ip(request) == "5.6.7.8"


def test_get_client_ip_falls_back_to_client_host():
    from app.rate_limit import get_client_ip

    request = _mock_request(headers={}, client_host="1.2.3.4")
    assert get_client_ip(request) == "1.2.3.4"


def test_get_client_ip_handles_missing_client():
    from app.rate_limit import get_client_ip

    request = MagicMock()
    request.headers = {}
    request.client = None
    assert get_client_ip(request) == "unknown"


def _mock_db_session(count):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_count_result = MagicMock()
    mock_count_result.scalar.return_value = count
    mock_session.execute = AsyncMock(return_value=mock_count_result)
    mock_session.commit = AsyncMock()
    return mock_session


@pytest.mark.asyncio
async def test_check_ip_rate_limit_passes_under_threshold():
    from app.rate_limit import check_ip_rate_limit

    mock_session = _mock_db_session(count=5)
    with patch("app.rate_limit.AsyncSessionLocal", return_value=mock_session):
        await check_ip_rate_limit("1.2.3.4")  # should not raise

    assert mock_session.commit.called


@pytest.mark.asyncio
async def test_check_ip_rate_limit_raises_429_at_threshold():
    from app.config import get_settings
    from app.rate_limit import check_ip_rate_limit

    settings = get_settings()
    mock_session = _mock_db_session(count=settings.max_requests_per_ip_per_hour)
    with patch("app.rate_limit.AsyncSessionLocal", return_value=mock_session):
        with pytest.raises(HTTPException) as exc_info:
            await check_ip_rate_limit("1.2.3.4")

    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_check_ip_rate_limit_raises_429_over_threshold():
    from app.config import get_settings
    from app.rate_limit import check_ip_rate_limit

    settings = get_settings()
    mock_session = _mock_db_session(count=settings.max_requests_per_ip_per_hour + 10)
    with patch("app.rate_limit.AsyncSessionLocal", return_value=mock_session):
        with pytest.raises(HTTPException) as exc_info:
            await check_ip_rate_limit("1.2.3.4")

    assert exc_info.value.status_code == 429
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
cd backend
uv run python -m pytest tests/test_rate_limit.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.rate_limit'`.

- [ ] **Step 5: Implement**

Create `backend/app/rate_limit.py`:

```python
from fastapi import HTTPException, Request
from sqlalchemy import text

from app.config import get_settings
from app.db import AsyncSessionLocal


def get_client_ip(request: Request) -> str:
    """Resolve the real client IP, preferring X-Forwarded-For (Render sits behind a proxy)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def check_ip_rate_limit(ip_address: str) -> None:
    """Raise 429 if ip_address has made too many /chat requests in the last hour.

    Otherwise records this request and prunes requests older than 24h so the
    table doesn't grow unbounded.
    """
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT count(*) FROM chat_requests "
                "WHERE ip_address = :ip AND created_at > NOW() - INTERVAL '1 hour'"
            ),
            {"ip": ip_address},
        )
        recent_count = result.scalar()

        if recent_count >= settings.max_requests_per_ip_per_hour:
            raise HTTPException(429, "Too many requests from this IP — try again later")

        await session.execute(
            text("INSERT INTO chat_requests (ip_address) VALUES (:ip)"),
            {"ip": ip_address},
        )
        await session.execute(
            text("DELETE FROM chat_requests WHERE created_at < NOW() - INTERVAL '24 hours'")
        )
        await session.commit()
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_rate_limit.py -v
```

Expected: 6 passed.

- [ ] **Step 7: Run the full backend suite**

```bash
uv run python -m pytest -m "not integration" -q
```

Expected: 79 passed (73 + 6 new), 3 deselected.

- [ ] **Step 8: Commit**

```bash
git add init.sql backend/app/config.py backend/app/rate_limit.py backend/tests/test_rate_limit.py
git commit -m "feat: add per-IP rate limiting for POST /chat"
```

---

## Task 2: Cost tracker

**Files:**
- Create: `backend/app/cost_tracker.py`
- Create: `backend/tests/test_cost_tracker.py`

**Interfaces:**
- Produces: `calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float`; `estimate_embedding_tokens(text: str) -> int`; `MODEL_PRICING: dict`.

### Context

Pure, deterministic functions — no I/O, no async. `calculate_cost` is used for both the chat completion calls (real token counts, from Task 3) and the embedding call estimate (this task provides both the pricing lookup and the estimate).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_cost_tracker.py`:

```python
from app.cost_tracker import calculate_cost, estimate_embedding_tokens


def test_calculate_cost_known_chat_model():
    # gpt-4o-mini: input 0.15/1M, output 0.60/1M
    cost = calculate_cost("openai/gpt-4o-mini", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 0.15 + 0.60


def test_calculate_cost_zero_tokens():
    assert calculate_cost("openai/gpt-4o-mini", 0, 0) == 0.0


def test_calculate_cost_unknown_model_returns_zero():
    assert calculate_cost("some/unknown-model", 1000, 1000) == 0.0


def test_calculate_cost_embedding_model_has_no_output_cost():
    # embeddings pricing has output=0.0, so output_tokens shouldn't affect cost
    cost = calculate_cost("openai/text-embedding-3-small", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 0.02


def test_estimate_embedding_tokens_basic():
    assert estimate_embedding_tokens("a" * 400) == 100


def test_estimate_embedding_tokens_minimum_one():
    assert estimate_embedding_tokens("") == 1
    assert estimate_embedding_tokens("hi") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
uv run python -m pytest tests/test_cost_tracker.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.cost_tracker'`.

- [ ] **Step 3: Implement**

Create `backend/app/cost_tracker.py`:

```python
MODEL_PRICING = {
    # USD per 1M tokens. Verify against https://openrouter.ai/models periodically — these drift.
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "openai/text-embedding-3-small": {"input": 0.02, "output": 0.0},
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a single API call. Unknown models cost 0.0."""
    pricing = MODEL_PRICING.get(model, {"input": 0.0, "output": 0.0})
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


def estimate_embedding_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — embed_text() doesn't expose real usage."""
    return max(1, len(text) // 4)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_cost_tracker.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Run the full backend suite**

```bash
uv run python -m pytest -m "not integration" -q
```

Expected: 85 passed (79 + 6 new), 3 deselected.

- [ ] **Step 6: Commit**

```bash
git add backend/app/cost_tracker.py backend/tests/test_cost_tracker.py
git commit -m "feat: add cost_tracker for token/cost estimation"
```

---

## Task 3: Wire rate limiting and cost tracking into `/chat`

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_chat.py` (full replacement — see below)

**Interfaces:**
- Consumes: `get_client_ip(request)`, `check_ip_rate_limit(ip_address)` (Task 1); `calculate_cost(model, input_tokens, output_tokens)`, `estimate_embedding_tokens(text)` (Task 2).
- Produces: `ChatResponse` gains `tokens_used: int`, `cost_usd: float`.

### Context

This is the integration task. Order of checks in `/chat` (cheapest-reject-first): message length (no I/O) → IP rate limit (one DB round trip) → conversation lookup/turn limit (existing DB round trips) → LLM calls. Token/cost accounting sums across every LLM `ainvoke()` call that actually happens this turn (1 or 2), plus an embedding estimate if `search_automation_patterns` fired.

- [ ] **Step 1: Write the failing tests — replace `backend/tests/test_chat.py` in full**

```python
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
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


def _make_ai_message(content=None, tool_calls=None, usage_metadata=None):
    """A minimal stand-in for a LangChain AIMessage: .content, .tool_calls, .usage_metadata."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    msg.usage_metadata = usage_metadata if usage_metadata is not None else {"input_tokens": 10, "output_tokens": 20}
    return msg


def _configure_mock_llm(mock_chat_openai_cls, ainvoke_side_effect):
    """Wire a patched `app.main.ChatOpenAI` class mock's .bind_tools().ainvoke chain."""
    mock_chat_openai_cls.return_value.bind_tools.return_value.ainvoke = AsyncMock(
        side_effect=ainvoke_side_effect
    )


async def _post_chat(payload):
    """POST /chat with the per-IP rate limit no-op'd (it has its own dedicated tests in
    test_rate_limit.py; individual chat tests shouldn't need to think about it)."""
    with patch("app.main.check_ip_rate_limit", new=AsyncMock()):
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
async def test_chat_returns_429_when_ip_rate_limit_exceeded():
    """POST /chat returns 429 when check_ip_rate_limit raises (IP over the hourly threshold).
    Deliberately does NOT use _post_chat, since that helper no-ops the IP check."""
    with patch(
        "app.main.check_ip_rate_limit",
        new=AsyncMock(side_effect=HTTPException(429, "Too many requests from this IP — try again later")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/chat", json={"message": "Hello"})

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


@pytest.mark.asyncio
async def test_chat_tokens_used_and_cost_reflect_llm_usage_metadata():
    """tokens_used and cost_usd are computed from the LLM's real usage_metadata."""
    mock_session = _mock_db_session(row=None)
    ai_msg = _make_ai_message(
        content="Here's some advice.",
        usage_metadata={"input_tokens": 100, "output_tokens": 50},
    )

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
    ):
        _configure_mock_llm(mock_chat_cls, [ai_msg])
        response = await _post_chat({"message": "How can I automate invoicing?"})

    data = response.json()
    assert data["tokens_used"] == 150
    expected_cost = (100 / 1_000_000) * 0.15 + (50 / 1_000_000) * 0.60
    assert data["cost_usd"] == round(expected_cost, 6)


@pytest.mark.asyncio
async def test_chat_tokens_used_includes_embedding_estimate_when_search_called():
    """tokens_used includes an estimated embedding token count when search_automation_patterns fires."""
    mock_session = _mock_db_session(row=None)
    tool_call = {"name": "search_automation_patterns", "args": {"task_description": "x" * 40}, "id": "call_6"}
    first_msg = _make_ai_message(
        content=None, tool_calls=[tool_call], usage_metadata={"input_tokens": 30, "output_tokens": 5}
    )
    second_msg = _make_ai_message(content="Use Zapier.", usage_metadata={"input_tokens": 60, "output_tokens": 20})

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.ChatOpenAI") as mock_chat_cls,
        patch(
            "app.main.search_automation_patterns",
            new=AsyncMock(return_value={"formatted": "...", "sources": []}),
        ),
    ):
        _configure_mock_llm(mock_chat_cls, [first_msg, second_msg])
        response = await _post_chat({"message": "What should I automate?"})

    data = response.json()
    # 30+5 (first call) + 60+20 (second call) + estimate_embedding_tokens("x"*40)=10
    assert data["tokens_used"] == 30 + 5 + 60 + 20 + 10
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
uv run python -m pytest tests/test_chat.py -v
```

Expected: FAIL — `app.main` doesn't yet import/use `check_ip_rate_limit`, `get_client_ip`, `calculate_cost`, `estimate_embedding_tokens`, and `ChatResponse` doesn't yet have `tokens_used`/`cost_usd`.

- [ ] **Step 3: Implement — modify `backend/app/main.py`**

Add to the imports at the top:

```python
from fastapi import FastAPI, Header, HTTPException, Query, Request
```

(adds `Request` to the existing import line)

Add two new imports after the existing `from mcp_server.server import capture_lead` line:

```python
from app.rate_limit import check_ip_rate_limit, get_client_ip
from app.cost_tracker import calculate_cost, estimate_embedding_tokens
```

Add `tokens_used`/`cost_usd` fields to `ChatResponse`:

```python
class ChatResponse(BaseModel):
    session_id: str
    reply: str
    sources: list[dict]  # [{title, similarity}, ...] — populated only if search_automation_patterns was called
    turns_remaining: int
    tokens_used: int
    cost_usd: float
```

Add a small helper function right before the `@app.post("/chat", ...)` decorator:

```python
def _usage_tokens(ai_message) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from an AIMessage's usage_metadata, defaulting to 0."""
    usage = getattr(ai_message, "usage_metadata", None) or {}
    return usage.get("input_tokens", 0), usage.get("output_tokens", 0)
```

Replace the `chat` function signature and body. The full new function:

```python
@app.post("/chat", tags=["chat"])
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Multi-turn chat with tool calling (search_automation_patterns, calculate_roi, capture_lead) via LangChain."""
    settings = get_settings()

    if len(body.message) > settings.max_input_chars:
        raise HTTPException(422, f"Message exceeds {settings.max_input_chars} chars")

    await check_ip_rate_limit(get_client_ip(request))

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
    total_input_tokens, total_output_tokens = _usage_tokens(ai_message)
    total_cost = calculate_cost(settings.chat_model, total_input_tokens, total_output_tokens)

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
                embedding_tokens = estimate_embedding_tokens(tool_args.get("task_description", ""))
                total_input_tokens += embedding_tokens
                total_cost += calculate_cost(settings.embedding_model, embedding_tokens, 0)
            elif tool_name == "capture_lead":
                tool_result = await capture_lead(**tool_args)
            else:
                tool_result = {"status": "error", "detail": f"Unknown tool {tool_name}"}

            messages.append(
                ToolMessage(content=json.dumps(tool_result), tool_call_id=tool_call["id"])
            )

        final_message = await llm.ainvoke(messages)
        reply = final_message.content or "I've made a note of that — could you tell me more?"
        final_in, final_out = _usage_tokens(final_message)
        total_input_tokens += final_in
        total_output_tokens += final_out
        total_cost += calculate_cost(settings.chat_model, final_in, final_out)
    else:
        reply = ai_message.content or "Could you tell me more about what you're looking to automate?"

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
        tokens_used=total_input_tokens + total_output_tokens,
        cost_usd=round(total_cost, 6),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_chat.py -v
```

Expected: 15 passed.

- [ ] **Step 5: Run the full backend suite**

```bash
uv run python -m pytest -m "not integration" -q
```

Expected: 88 passed (85 - 12 old test_chat tests + 15 new test_chat tests = 88). Don't take this on faith — run it and confirm the actual number; the important invariant is that no test outside `test_chat.py` regresses, and every test in the new `test_chat.py` passes.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_chat.py
git commit -m "feat: wire per-IP rate limiting and token/cost tracking into /chat"
```

---

## Task 4: Gradio UI — show tokens/cost, clearer rate-limit messaging

**Files:**
- Modify: `frontend/app.py`

**Interfaces:**
- Consumes: `POST /chat` response fields `tokens_used`, `cost_usd` (Task 3), and the `detail` field on `429` error responses (now two different messages depending on which limit fired).

### Context

The current Gradio UI hardcodes `"Session limit reached. Please refresh to start a new conversation."` for any `429`, which is now misleading — a `429` can also mean the per-IP rate limit fired, with a different actual cause. Fix that, and surface the new cost/token fields the same way `turns_remaining` is already surfaced.

- [ ] **Step 1: Modify `frontend/app.py`'s `chat()` function**

Replace the body of `chat()` (the whole function, `frontend/app.py:13-41`) with:

```python
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
            detail = data.get("detail", "Rate limit reached")
            return f"{detail} Please try again later or start a new conversation.", history
        if resp.status_code != 200:
            return f"Error {resp.status_code}: {data.get('detail', 'Unknown error')}", history

        reply = data["reply"]
        turns_left = data.get("turns_remaining", "?")
        sources = data.get("sources", [])
        tokens_used = data.get("tokens_used", 0)
        cost_usd = data.get("cost_usd", 0.0)

        if sources:
            citations = "\n\n**Sources used:**\n" + "\n".join(
                f"- {s['title']} (similarity: {s['similarity']:.2f})" for s in sources
            )
            reply += citations

        reply += f"\n\n*{turns_left} turns remaining · {tokens_used} tokens · ${cost_usd:.6f} this turn.*"
        history.append((message, reply))
        return "", history
    except Exception as e:
        return f"Connection error: {e}", history
```

- [ ] **Step 2: Verify the module still imports cleanly**

```bash
cd backend
uv run python -c "import sys; sys.path.insert(0, '../frontend'); import app"
```

Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add frontend/app.py
git commit -m "feat: show tokens/cost per turn in Gradio UI, clearer rate-limit messaging"
```

---

## Task 5: Final verification + README

### Steps

- [ ] **Step 1: Run the full backend suite**

```bash
cd backend
uv run python -m pytest -m "not integration" -q
```

Expected: all passing, matching Task 3 Step 5's confirmed count. Report the exact number.

- [ ] **Step 2: Manual verification against a running server**

This requires a real Postgres with `chat_requests` applied (see Task 1 Step 1's note — run the `CREATE TABLE`/`CREATE INDEX` manually if testing against `twenty-db-1` or any pre-existing DB, since `init.sql` won't re-run automatically).

```sql
CREATE TABLE IF NOT EXISTS chat_requests (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ip_address TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_requests_ip_created_idx
    ON chat_requests (ip_address, created_at);
```

Then:

```bash
uv run python -m uvicorn app.main:app --port 8001   # or another free port
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I spend 4 hours a week on invoicing"}'
```

Confirm the response includes non-zero `tokens_used` and `cost_usd` alongside the existing fields.

- [ ] **Step 3: Update `README.md`**

In the "Sprint 2 Requirement Coverage" Medium-optional table, change:

```
| Calculate and display token usage and costs | Medium | ⬜ Not done |
```

(this line currently lives inside the combined "Not done" row — split it out) to:

```
| Calculate and display token usage and costs | Medium | ✅ `tokens_used`/`cost_usd` on every `/chat` response; real chat-completion token counts via LangChain's `usage_metadata`, estimated embedding tokens |
```

Update the summary line "**Current count toward '2 Medium + 1 Hard' max-points target: 1 Medium + 1 Hard.**" to "**2 Medium + 1 Hard**" and remove the "Real gap, not yet closed" framing for this specific item (the other roadmap gaps remain).

Add a new bullet to the "Security Measures" section, after the "CORS allowlist" bullet:

```
- **Per-IP rate limiting** — `chat_requests` table logs every `/chat` call by IP; more than `max_requests_per_ip_per_hour` (default 30) in a rolling hour returns `429`, independent of `session_id` (closes the session-cycling bypass — a script can't dodge the per-session turn limit just by generating a fresh `session_id` every call).
```

In the "Roadmap / Known Gaps" section, remove the bullet "**Only 1 Medium + 1 Hard optional task done**..." and replace it with:

```
- **2 Medium + 1 Hard optional tasks done** — real-time KB updates, MCP-server tools, and token/cost tracking. Still short of the stretch goal of adding more (e.g. conversation export, multi-model support) if pursued further.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: update README for token/cost tracking + per-IP rate limiting"
```
