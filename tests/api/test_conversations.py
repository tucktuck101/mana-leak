"""`POST/GET /conversations`, `GET /conversations/{id}` (`docs/contracts.md`
-> REST API). Uses a small fake conversation service, injected through
FastAPI dependency overrides (plan -> WP5 Notes) -- `mana_leak_core.conversations`
doesn't exist until WP4 merges.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from mana_leak_api.dependencies import (
    get_create_conversation,
    get_get_conversation,
    get_list_conversations,
)
from mana_leak_api.main import app
from mana_leak_core.contracts.conversations import ConversationDetail, ConversationSummary
from mana_leak_core.contracts.errors import ErrorCode, ManaLeakError

pytestmark = pytest.mark.anyio


def _summary(conversation_id: UUID, title: str | None = None) -> ConversationSummary:
    now = datetime.now(UTC)
    return ConversationSummary(id=conversation_id, title=title, created_at=now, updated_at=now)


async def test_create_conversation_returns_201_summary(client: httpx.AsyncClient) -> None:
    created_id = uuid4()

    async def fake_create_conversation(title: str | None = None) -> ConversationSummary:
        assert title == "my deck"
        return _summary(created_id, title=title)

    app.dependency_overrides[get_create_conversation] = lambda: fake_create_conversation

    response = await client.post("/conversations", json={"title": "my deck"})

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(created_id)
    assert body["title"] == "my deck"


async def test_create_conversation_without_title_defaults_to_none(
    client: httpx.AsyncClient,
) -> None:
    async def fake_create_conversation(title: str | None = None) -> ConversationSummary:
        assert title is None
        return _summary(uuid4(), title=None)

    app.dependency_overrides[get_create_conversation] = lambda: fake_create_conversation

    response = await client.post("/conversations", json={})

    assert response.status_code == 201
    assert response.json()["title"] is None


async def test_list_conversations_default_limit_is_50(client: httpx.AsyncClient) -> None:
    seen_limits: list[int] = []

    async def fake_list_conversations(limit: int = 50) -> list[ConversationSummary]:
        seen_limits.append(limit)
        return [_summary(uuid4()), _summary(uuid4())]

    app.dependency_overrides[get_list_conversations] = lambda: fake_list_conversations

    response = await client.get("/conversations")

    assert response.status_code == 200
    assert seen_limits == [50]
    assert len(response.json()) == 2


async def test_list_conversations_passes_through_explicit_limit(client: httpx.AsyncClient) -> None:
    seen_limits: list[int] = []

    async def fake_list_conversations(limit: int = 50) -> list[ConversationSummary]:
        seen_limits.append(limit)
        return []

    app.dependency_overrides[get_list_conversations] = lambda: fake_list_conversations

    response = await client.get("/conversations", params={"limit": 5})

    assert response.status_code == 200
    assert seen_limits == [5]


async def test_get_conversation_returns_detail(client: httpx.AsyncClient) -> None:
    conversation_id = uuid4()

    async def fake_get_conversation(conv_id: UUID) -> ConversationDetail:
        assert conv_id == conversation_id
        summary = _summary(conversation_id, title="t")
        return ConversationDetail(**summary.model_dump(), messages=[])

    app.dependency_overrides[get_get_conversation] = lambda: fake_get_conversation

    response = await client.get(f"/conversations/{conversation_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(conversation_id)
    assert body["messages"] == []


async def test_get_unknown_conversation_returns_404_not_found(client: httpx.AsyncClient) -> None:
    async def fake_get_conversation(conv_id: UUID) -> ConversationDetail:
        raise ManaLeakError(ErrorCode.not_found, "conversation not found")

    app.dependency_overrides[get_get_conversation] = lambda: fake_get_conversation

    response = await client.get(f"/conversations/{uuid4()}")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"


async def test_get_conversation_with_malformed_id_returns_422_validation_error(
    client: httpx.AsyncClient,
) -> None:
    # FastAPI resolves this route's `Depends()` alongside path-parameter
    # parsing, before raising the validation error, so it must be overridden
    # even though a malformed UUID never reaches the handler body.
    async def unreachable_get_conversation(conv_id: UUID) -> ConversationDetail:
        raise AssertionError("should not be called: UUID parsing fails first")

    app.dependency_overrides[get_get_conversation] = lambda: unreachable_get_conversation

    response = await client.get("/conversations/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
