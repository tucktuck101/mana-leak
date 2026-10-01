"""`GET /health` (AC-8; WP5 checklist N/A -- `HealthResponse` shape is WP1's,
exercised here through the live route).

The "database reachable" case uses the real `get_session()` dependency
unoverridden (Compose Postgres is already running, per task setup -- no
migration needed, `SELECT 1` doesn't touch application tables). The
"database unreachable" case overrides `get_session` with a fake whose
`execute()` raises, so this test never depends on actually tearing down
Postgres.

`langfuse` now reports `mana_leak_core.tracing.current_health()` (WP6,
FR-7) instead of a hardcoded `"disabled"`. `_tracing_env` strips any ambient
`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` from the process environment
(e.g. an unrelated agent sandbox's own observability config) before every
test in this module and resets `tracing`'s module-level client cache/health
state after it, so this suite's results depend only on what each test
configures, never on `.env`/the ambient shell (M2 plan: "no test depends on
`.env` for Langfuse configuration").
"""

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from mana_leak_api.main import app
from mana_leak_core import tracing
from mana_leak_core.db.session import get_session
from mana_leak_core.settings import get_settings

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _tracing_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    tracing._get_client.cache_clear()
    tracing._health = None
    yield
    get_settings.cache_clear()
    # `_get_client` may still be a test's own monkeypatched callable here --
    # `monkeypatch`'s own teardown (same fixture, running after this one)
    # restores the real `lru_cache`d function -- so only clear the real
    # function's cache if it is still the one installed.
    if hasattr(tracing._get_client, "cache_clear"):
        tracing._get_client.cache_clear()
    tracing._health = None


class _FakeLangfuseClient:
    """Stands in for `tracing._get_client()`'s return value: only
    `auth_check()` (what `refresh_health()` calls) needs to behave."""

    def __init__(self, *, authenticated: bool | None = True) -> None:
        self._authenticated = authenticated

    def auth_check(self) -> bool:
        if self._authenticated is None:
            raise RuntimeError("401: public/secret key mismatch")
        return self._authenticated


async def test_health_ok_when_database_reachable(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["langfuse"] == "disabled"
    assert body["rules_version"] is None
    assert body["card_source_version"] is None


async def test_health_degraded_when_database_unreachable(client: httpx.AsyncClient) -> None:
    class _BrokenSession:
        async def execute(self, *args: object, **kwargs: object) -> None:
            raise ConnectionRefusedError("database unreachable")

    async def _broken_get_session() -> AsyncIterator[_BrokenSession]:
        yield _BrokenSession()

    app.dependency_overrides[get_session] = _broken_get_session

    response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] == "unavailable"
    assert body["langfuse"] == "disabled"


async def test_health_reports_ok_when_langfuse_is_healthy(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tracing, "current_health", lambda: "ok")

    response = await client.get("/health")

    assert response.status_code == 200  # langfuse never drives the status code
    assert response.json()["langfuse"] == "ok"


async def test_health_reports_unavailable_when_langfuse_is_unreachable(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tracing, "current_health", lambda: "unavailable")

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["langfuse"] == "unavailable"


async def test_health_reflects_a_forced_immediate_refresh(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-7: "a test may force an immediate refresh rather than wait" out
    the 30 s background loop interval -- `/health` must reflect the result
    of a directly-awaited `refresh_health()` call, not only the loop's own
    schedule."""
    monkeypatch.setattr(tracing, "_get_client", lambda: _FakeLangfuseClient(authenticated=True))

    await tracing.refresh_health()
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["langfuse"] == "ok"


async def test_health_reports_unavailable_when_keys_are_mismatched(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-10: a mismatched key pair makes `auth_check()` raise (this SDK's
    usual behaviour on an auth failure, per WP3) -- `refresh_health()` must
    map that to `unavailable`, never leave `/health`'s status code
    affected."""
    monkeypatch.setattr(tracing, "_get_client", lambda: _FakeLangfuseClient(authenticated=None))

    await tracing.refresh_health()
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["langfuse"] == "unavailable"


def test_lifespan_runs_an_immediate_health_refresh_and_shuts_down_cleanly() -> None:
    """F13: before the lifespan's first refresh ever runs (every other test
    in this module, which never sends ASGI lifespan events), `_health` stays
    `None` and `current_health()` falls back to its unset-keys default.
    `with TestClient(app) as test_client:` sends real `startup`/`shutdown`
    lifespan events -- proving the background refresh loop actually starts
    (an immediate refresh explicitly sets `_health`, no longer `None`) and
    that shutdown (task cancellation + `tracing.shutdown()`) completes
    without raising."""
    assert tracing._health is None

    with TestClient(app) as test_client:
        response = test_client.get("/health")
        assert response.status_code in (200, 503)
        assert tracing._health is not None  # the lifespan's immediate refresh already ran
