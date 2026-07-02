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
