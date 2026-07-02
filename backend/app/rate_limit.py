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
