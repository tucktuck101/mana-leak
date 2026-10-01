"""`GET /health` (AC-8; WP5 checklist N/A -- `HealthResponse` shape is WP1's,
exercised here through the live route).

The "database reachable" case uses the real `get_session()` dependency
unoverridden (Compose Postgres is already running, per task setup -- no
migration needed, `SELECT 1` doesn't touch application tables). The
"database unreachable" case overrides `get_session` with a fake whose
`execute()` raises, so this test never depends on actually tearing down
Postgres.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from mana_leak_api.main import app
from mana_leak_core.db.session import get_session

pytestmark = pytest.mark.anyio


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
