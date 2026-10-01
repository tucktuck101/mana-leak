"""Shared pytest fixtures (AGENTS.md boundary file, WP1).

`db` targets the dedicated `mana_leak_test` database (never the Compose
`mana_leak` database used for the AC-12 manual demo), migrates it to head
once per session if `packages/core/alembic.ini` exists (added by WP2), and
truncates every application table after each test that uses it. If
`mana_leak_test` is unreachable (Postgres not running locally), DB-backed
tests are skipped, not failed.
"""

import os
import subprocess
from collections.abc import AsyncIterator, Generator
from pathlib import Path

import psycopg
import pytest
from mana_leak_core.settings import get_settings
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = REPO_ROOT / "packages" / "core" / "alembic.ini"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


def _test_database_url() -> str:
    """`mana_leak_test`, same host/credentials as `Settings().database_url`.

    `render_as_string(hide_password=False)` is required here: plain `str()`
    on a SQLAlchemy `URL` masks the password as `***`, which made every
    probe/connection in this fixture fail silently (DB-backed tests always
    skipped instead of running) - a bug fixed by WP2 (`AGENTS.md` SS56).
    """
    url = make_url(get_settings().database_url.get_secret_value()).set(database="mana_leak_test")
    return url.render_as_string(hide_password=False)


def _sync_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def _migrated_test_db() -> Generator[str | None]:
    url = _test_database_url()
    try:
        with psycopg.connect(_sync_dsn(url), connect_timeout=2):
            pass
    except psycopg.OperationalError:
        yield None
        return

    if ALEMBIC_INI.exists():
        env = os.environ.copy()
        env["DATABASE_URL"] = url
        subprocess.run(
            ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            check=True,
        )
    yield url


@pytest.fixture
async def db(_migrated_test_db: str | None) -> AsyncIterator[AsyncSession]:
    """An `AsyncSession` against `mana_leak_test`, truncated after the test."""
    if _migrated_test_db is None:
        pytest.skip("mana_leak_test database is unreachable")

    engine = create_async_engine(_migrated_test_db, pool_pre_ping=True)
    try:
        async with AsyncSession(engine) as session:
            yield session
    finally:
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql(
                "select tablename from pg_tables"
                " where schemaname = 'public' and tablename != 'alembic_version'"
            )
            tables = [row[0] for row in result]
            if tables:
                quoted = ", ".join(f'"{table}"' for table in tables)
                await conn.exec_driver_sql(f"truncate {quoted} restart identity cascade")
        await engine.dispose()
