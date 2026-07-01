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
    """Health check must return 200 even when no auth header is sent.
    Render uptime monitor calls this without credentials."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Deliberately send no Authorization header — must still get 200
        response = await client.get("/health", headers={})
    assert response.status_code == 200
    # And confirm a request WITH an unrecognized auth header also works (no auth enforcement)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health", headers={"X-Admin-Key": "wrong-key"})
    assert response.status_code == 200


# --- /admin/ingest endpoint tests ---

from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_admin_ingest_requires_api_key():
    """POST /admin/ingest without X-Admin-Key returns 401."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/admin/ingest")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_ingest_rejects_wrong_key():
    """POST /admin/ingest with wrong key returns 403."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/admin/ingest", headers={"X-Admin-Key": "wrong-key"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_ingest_static_calls_ingest_static_kb():
    """POST /admin/ingest?source=static with correct key calls ingest_static_kb."""
    from app.config import get_settings

    correct_key = get_settings().admin_api_key
    mock_result = {"ingested": 6, "skipped": 0}

    with patch("app.main.ingest_static_kb", new=AsyncMock(return_value=mock_result)):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/ingest?source=static",
                headers={"X-Admin-Key": correct_key},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "static"
    assert data["result"]["ingested"] == 6


@pytest.mark.asyncio
async def test_admin_ingest_defaults_to_static():
    """POST /admin/ingest without ?source param defaults to static mode."""
    from app.config import get_settings

    correct_key = get_settings().admin_api_key

    with patch("app.main.ingest_static_kb", new=AsyncMock(return_value={"ingested": 0, "skipped": 6})):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/ingest",
                headers={"X-Admin-Key": correct_key},
            )

    assert response.status_code == 200
    assert response.json()["source"] == "static"


@pytest.mark.asyncio
async def test_admin_ingest_live_calls_ingest_live_docs():
    """POST /admin/ingest?source=live with correct key calls ingest_live_docs."""
    from app.config import get_settings

    correct_key = get_settings().admin_api_key
    mock_result = {"ingested": 14, "skipped": 0, "errors": 0}

    with patch("app.main.ingest_live_docs", new=AsyncMock(return_value=mock_result)):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/ingest?source=live",
                headers={"X-Admin-Key": correct_key},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "live"
    assert data["result"]["ingested"] == 14


@pytest.mark.asyncio
async def test_admin_ingest_invalid_source_returns_422():
    """POST /admin/ingest?source=bogus returns 422 (invalid query param value)."""
    from app.config import get_settings

    correct_key = get_settings().admin_api_key

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/admin/ingest?source=bogus",
            headers={"X-Admin-Key": correct_key},
        )

    assert response.status_code == 422
