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
