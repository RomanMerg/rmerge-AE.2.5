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


def _fake_docs(n=3):
    return [
        {
            "title": f"Doc {i}",
            "content": f"content {i}" * 20,
            "metadata": {},
            "similarity": 0.9 - i * 0.1,
        }
        for i in range(n)
    ]


def _mock_llm_client(reply="Here is your automation advice."):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = reply
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    return mock_client


async def _post_chat(payload):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/chat", json=payload)


@pytest.mark.asyncio
async def test_chat_new_session_returns_200_with_new_uuid_session_id():
    """POST /chat with no session_id returns 200 with a new UUID session_id."""
    mock_session = _mock_db_session(row=None)  # no existing session -> INSERT path

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch("app.main.search_documents", new=AsyncMock(return_value=_fake_docs())),
        patch("app.main.AsyncOpenAI", return_value=_mock_llm_client()),
    ):
        response = await _post_chat({"message": "How can I automate invoicing?"})

    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    # Confirm it's a valid UUID string
    uuid.UUID(data["session_id"])


@pytest.mark.asyncio
async def test_chat_existing_session_increments_turn_count():
    """Second call with same session_id reuses the session (turns_remaining decrements)."""
    session_id = str(uuid.uuid4())
    existing_row = MagicMock(turn_count=2, history=[])
    mock_session = _mock_db_session(row=existing_row)

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch("app.main.search_documents", new=AsyncMock(return_value=_fake_docs())),
        patch("app.main.AsyncOpenAI", return_value=_mock_llm_client()),
    ):
        response = await _post_chat({"session_id": session_id, "message": "Automate onboarding"})

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == session_id
    settings = get_settings()
    # turn_count was 2 before this call -> after increment it's 3
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

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch("app.main.search_documents", new=AsyncMock(return_value=_fake_docs())),
        patch("app.main.AsyncOpenAI", return_value=_mock_llm_client(reply=expected_reply)),
    ):
        response = await _post_chat({"message": "Help me automate follow-ups"})

    assert response.status_code == 200
    assert response.json()["reply"] == expected_reply


@pytest.mark.asyncio
async def test_chat_sources_have_title_and_similarity_keys():
    """sources list contains up to 3 items with title and similarity fields."""
    mock_session = _mock_db_session(row=None)
    docs = _fake_docs(3)

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch("app.main.search_documents", new=AsyncMock(return_value=docs)),
        patch("app.main.AsyncOpenAI", return_value=_mock_llm_client()),
    ):
        response = await _post_chat({"message": "What should I automate first?"})

    assert response.status_code == 200
    sources = response.json()["sources"]
    assert len(sources) == 3
    for source in sources:
        assert set(source.keys()) == {"title", "similarity"}


@pytest.mark.asyncio
async def test_chat_turns_remaining_decrements_correctly():
    """turns_remaining = max_turns_per_session - (turn_count + 1)."""
    settings = get_settings()
    session_id = str(uuid.uuid4())
    existing_row = MagicMock(turn_count=0, history=[])
    mock_session = _mock_db_session(row=existing_row)

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch("app.main.search_documents", new=AsyncMock(return_value=_fake_docs())),
        patch("app.main.AsyncOpenAI", return_value=_mock_llm_client()),
    ):
        response = await _post_chat({"session_id": session_id, "message": "First question"})

    assert response.status_code == 200
    assert response.json()["turns_remaining"] == settings.max_turns_per_session - 1
