"""Async session factory (`docs/contracts.md` -> Core service interfaces
shared-contracts table (WP2): `get_session()` on
`create_async_engine(settings.database_url, pool_pre_ping=True)`).

`pool_pre_ping=True` is what makes a long-lived engine survive a Postgres
restart (AC-3, NFR-3): without it, a bare engine keeps handing out pooled
connections that went stale across the restart until the first query fails.
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mana_leak_core.settings import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Process-wide engine, built from `Settings().database_url` on first use.

    Cached like `get_settings()` so repeated calls share one connection
    pool; tests that need a different `database_url` call `get_engine.cache_clear()`
    (and `get_settings.cache_clear()`) after changing the environment.
    """
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-dependency-shaped async session factory: one session per call,
    closed when the caller is done with it."""
    async with _session_factory()() as session:
        yield session
