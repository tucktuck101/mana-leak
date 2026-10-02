"""Shared pytest fixtures (AGENTS.md boundary file, WP1; M2-03 fix).

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

`_langfuse_env_isolated` (autouse) strips `LANGFUSE_PUBLIC_KEY`/
`LANGFUSE_SECRET_KEY` from the process environment and resets
`mana_leak_core.tracing`'s module-level client/health caches before and
after every test in the suite, so no test anywhere can build a real
Langfuse client (and export spans over the network) from a developer's own
shell environment (M2-03). Tests that need tracing enabled opt back in
explicitly inside the test body.
"""

import logging
import os
import re
import subprocess
from collections.abc import AsyncIterator, Generator
from pathlib import Path

import psycopg
import pytest
from mana_leak_core import tracing
from mana_leak_core.settings import get_settings
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = REPO_ROOT / "packages" / "core" / "alembic.ini"

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _langfuse_env_isolated() -> Generator[None]:
    """No test can build a real Langfuse client from the developer's shell
    (M2 plan, Execution model: "no test depends on `.env` for Langfuse
    configuration"). `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are the only
    two variables `tracing.py`'s client construction reads from `Settings`
    (`.env` itself never carries them -- only the Compose-only
    `LANGFUSE_INIT_*` pair does); strip them from the ambient process
    environment (e.g. an unrelated shell/sandbox's own observability config)
    before every test in the whole suite, and reset `get_settings`'s and
    `tracing`'s module-level caches so a leftover client/health state never
    leaks between tests. Tests that need tracing enabled opt back in
    explicitly inside the test body, e.g. `tests/core/test_turn.py`'s
    `real_langfuse_client` fixture (`monkeypatch.setenv(...)`), never by
    relying on a key pair already present in the environment.

    Deliberately plain `os.environ` save/restore, not the `monkeypatch`
    fixture: requesting `monkeypatch` here would make *this* autouse
    fixture the first thing to instantiate it for the test, which flips
    `monkeypatch`'s own finalizer to run before sibling autouse fixtures
    that don't depend on it (e.g. `tests/core/conftest.py`'s
    `_reset_tracing_cache`) instead of after -- those fixtures then run
    their teardown against a still-monkeypatched `tracing._get_client`
    (e.g. `span_capture_factory`'s lambda), which has no `cache_clear`.
    Plain `os.environ` mutation participates in no such ordering.
    """
    langfuse_keys = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    saved = {name: os.environ.pop(name, None) for name in langfuse_keys}
    get_settings.cache_clear()
    tracing._get_client.cache_clear()
    tracing._health = None
    yield
    for name, value in saved.items():
        if value is not None:
            os.environ[name] = value
    get_settings.cache_clear()
    # `_get_client` may still be a test's own monkeypatched callable here --
    # `monkeypatch`'s own teardown (a different fixture, instantiated and
    # torn down entirely within whichever test/fixture actually requested
    # it) restores the real `lru_cache`d function -- so only clear the real
    # function's cache if it is still the one installed.
    if hasattr(tracing._get_client, "cache_clear"):
        tracing._get_client.cache_clear()
    tracing._health = None


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
