"""Shared pytest fixtures (AGENTS.md boundary file, WP1).

`db` targets a per-worktree schema (`test_<slug>`, `<slug>` derived from this
worktree's own checkout-directory name) inside the shared `mana_leak_test`
database (never the Compose `mana_leak` database used for the AC-12 manual
demo) -- so parallel workmux lanes sharing one `mana_leak_test` database never
corrupt each other's runs (`docs/ROADMAP.md`:654). The schema is created on
first use, migrated to head once per session if `packages/core/alembic.ini`
exists (added by WP2), and every table inside it is truncated after each test
that uses it. The schema is dropped at session end. If `mana_leak_test` is
unreachable (Postgres not running locally), DB-backed tests are skipped, not
failed.
"""

import logging
import os
import re
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

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


def _worktree_schema() -> str:
    """`test_<slug>`, `<slug>` derived from this worktree's own checkout
    directory name (workmux's lane handle, `worktree_naming: full`) so every
    lane gets its own schema inside the shared `mana_leak_test` database
    without a new env var or workmux hook."""
    slug = re.sub(r"[^a-z0-9]+", "_", REPO_ROOT.name.lower()).strip("_")
    return f"test_{slug}"


def _test_database_url(schema: str | None = None) -> str:
    """`mana_leak_test`, same host/credentials as `Settings().database_url`.

    `render_as_string(hide_password=False)` is required here: plain `str()`
    on a SQLAlchemy `URL` masks the password as `***`, which made every
    probe/connection in this fixture fail silently (DB-backed tests always
    skipped instead of running) - a bug fixed by WP2 (`AGENTS.md` SS56).

    When `schema` is given, the URL carries `?options=-csearch_path%3D<schema>`
    (schema-only, never `<schema>,public`: with `,public` present, alembic
    finds `public.alembic_version` already at head from another lane and
    creates nothing in the new schema) so the sync alembic engine, the async
    app engine, and any libpq-based probe all honour the same search path.
    """
    url = make_url(get_settings().database_url.get_secret_value()).set(database="mana_leak_test")
    if schema is not None:
        url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    return url.render_as_string(hide_password=False)


def _sync_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def _migrated_test_db() -> Generator[str | None]:
    schema = _worktree_schema()
    base_url = _test_database_url()
    scoped_url = _test_database_url(schema=schema)
    try:
        with psycopg.connect(_sync_dsn(base_url), connect_timeout=2, autocommit=True) as conn:
            conn.execute(f'create schema if not exists "{schema}"')
    except psycopg.OperationalError:
        yield None
        return

    if ALEMBIC_INI.exists():
        env = os.environ.copy()
        env["DATABASE_URL"] = scoped_url
        subprocess.run(
            ["uv", "run", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            check=True,
        )
    try:
        yield scoped_url
    finally:
        try:
            with psycopg.connect(_sync_dsn(base_url), connect_timeout=2, autocommit=True) as conn:
                conn.execute(f'drop schema if exists "{schema}" cascade')
        except Exception:
            logger.warning("failed to drop test schema %r during teardown", schema, exc_info=True)


@pytest.fixture
async def db(_migrated_test_db: str | None) -> AsyncIterator[AsyncSession]:
    """An `AsyncSession` against the worktree's schema, truncated after the test."""
    if _migrated_test_db is None:
        pytest.skip("mana_leak_test database is unreachable")

    schema = _worktree_schema()
    engine = create_async_engine(_migrated_test_db, pool_pre_ping=True)
    try:
        async with AsyncSession(engine) as session:
            yield session
    finally:
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql(
                "select tablename from pg_tables"
                f" where schemaname = '{schema}' and tablename != 'alembic_version'"
            )
            tables = [row[0] for row in result]
            if tables:
                quoted = ", ".join(f'"{table}"' for table in tables)
                await conn.exec_driver_sql(f"truncate {quoted} restart identity cascade")
        await engine.dispose()
