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
