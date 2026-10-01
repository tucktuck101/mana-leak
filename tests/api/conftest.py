"""Shared fixtures for `tests/api` (WP5 acceptance: `uv run pytest tests/api`).

`client` drives the real FastAPI app in-process over `httpx.ASGITransport`
(no real socket -- fine for request/response and normal-flow streaming
assertions; real client-disconnect semantics are an e2e concern, WP8). Each
test gets `app.dependency_overrides` cleared before and after; nothing in
this suite overrides `process_turn`/the conversation service any more (WP4
has merged) -- the only remaining override use is the pre-handler 422 body-
validation test, which never reaches a dependency that touches the database.

`api_db` points the process-wide `get_session()` factory that
`mana_leak_core.conversations`/`orchestrator` call directly -- not through
FastAPI dependency injection, so overriding a `mana_leak_api.dependencies`
provider would never reach them -- at the migrated `mana_leak_test`
database the root `db` fixture (`tests/conftest.py`) already prepared and
will truncate afterwards. Mirrors `tests/core/test_turn.py`'s `turn_db`
fixture (WP4): both need the same cache-clearing dance because
`get_settings()`/`get_engine()`/`_session_factory()` are all `lru_cache`d.

`fake_gateway` patches `mana_leak_core.orchestrator.complete` with a
scripted stand-in for the model gateway. The gateway is WP3's own contract,
not `process_turn` or the conversation service, so faking it here only
controls model-answer timing/content -- the same thing
`tests/core/test_turn.py`'s fixture of the same name does for the
orchestrator's own test suite -- without requiring a live network call for
every non-`live` test.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from mana_leak_api.main import app
from mana_leak_core import db as db_module
from mana_leak_core import orchestrator
from mana_leak_core.gateway import ModelChunk
from mana_leak_core.settings import get_settings

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides.clear()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def api_db(
    db: object, _migrated_test_db: str | None, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    if _migrated_test_db is None:  # pragma: no cover - `db` already skipped
        pytest.skip("mana_leak_test database is unreachable")

    monkeypatch.setenv("DATABASE_URL", _migrated_test_db)
    get_settings.cache_clear()
    db_module.get_engine.cache_clear()
    db_module.session._session_factory.cache_clear()
    try:
        yield
    finally:
        await db_module.get_engine().dispose()
        get_settings.cache_clear()
        db_module.get_engine.cache_clear()
        db_module.session._session_factory.cache_clear()


class FakeGateway:
    """Scripted stand-in for `mana_leak_core.gateway.complete`. `called` and
    `release` let a test synchronize with exactly the point in the real
    `process_turn` where the per-conversation lock is already held (the
    model call happens after `message_start` is yielded and the lock is
    acquired -- orchestrator.py's `_run_turn`) but the turn hasn't finished,
    without needing a live model call to create that window."""

    def __init__(
        self, *, deltas: tuple[str, ...] = ("hi",), hang_after_deltas: bool = False
    ) -> None:
        self.deltas = deltas
        self.hang_after_deltas = hang_after_deltas
        self.called = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[ModelChunk]:
        self.called.set()
        return self._stream()

    async def _stream(self) -> AsyncIterator[ModelChunk]:
        for delta in self.deltas:
            yield ModelChunk(delta=delta)
        if self.hang_after_deltas:
            await self.release.wait()
        yield ModelChunk(delta="", finish_reason="stop")


@pytest.fixture
def fake_gateway(monkeypatch: pytest.MonkeyPatch):
    def _install(**kwargs: Any) -> FakeGateway:
        fake = FakeGateway(**kwargs)
        monkeypatch.setattr(orchestrator, "complete", fake.complete)
        return fake

    return _install
