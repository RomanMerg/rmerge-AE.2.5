from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

engine = create_async_engine(
    get_settings().database_url,
    echo=False,          # Set True locally to log SQL queries; keep False in prod
    pool_pre_ping=True,  # Drop stale connections before use — prevents 500s after idle
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models in this project."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session, guarantees close on exit."""
    async with AsyncSessionLocal() as session:
        yield session
