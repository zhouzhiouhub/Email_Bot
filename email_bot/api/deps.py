"""
FastAPI dependency injectors.
"""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

_engine = create_async_engine(settings.database_url, echo=False)
_SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with _SessionLocal() as session:
        yield session


def get_session_factory():
    """Return the session factory for use inside LangGraph nodes."""
    return _SessionLocal
