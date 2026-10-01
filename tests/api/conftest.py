"""Shared fixtures for `tests/api` (WP5 acceptance: `uv run pytest tests/api`).

`client` drives the real FastAPI app in-process over `httpx.ASGITransport`
(no real socket -- fine for request/response and normal-flow streaming
assertions; real client-disconnect semantics are an e2e concern, WP8). Each
test gets `app.dependency_overrides` cleared before and after, so one test's
fake `process_turn`/conversation-service override never leaks into another.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from mana_leak_api.main import app

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
