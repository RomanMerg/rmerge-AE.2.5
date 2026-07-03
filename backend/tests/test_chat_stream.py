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


async def test_stream_recursion_limit_emits_done_with_fallback_reply():
    """Recursion overflow must mirror /chat: graceful done event with the fallback
    reply and a consumed turn — not a generic error event."""
    from langchain_core.messages import AIMessage

    from app.main import RECURSION_FALLBACK_REPLY

    def _always_tool_call(*args, **kwargs):
        return AIMessage(
            content="",
            tool_calls=[{
                "name": "calculate_roi",
                "args": {"hours_saved_per_week": 1, "hourly_rate": 10, "setup_cost": 100},
                "id": f"call_{uuid.uuid4().hex[:6]}",
            }],
            usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )

    mock_session = _mock_db_session(row=None)
    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.agent.ChatOpenAI") as mock_chat_cls,
    ):
        mock_chat_cls.return_value.bind_tools.return_value.ainvoke = AsyncMock(
            side_effect=_always_tool_call
        )
        response = await _post_stream({"message": "loop forever"})

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [e for e, _ in events if e == "error"] == []
    done_events = [d for e, d in events if e == "done"]
    assert len(done_events) == 1
    assert done_events[0]["reply"] == RECURSION_FALLBACK_REPLY
    assert done_events[0]["tokens_used"] == 0
