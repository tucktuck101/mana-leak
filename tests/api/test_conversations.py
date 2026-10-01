"""`POST/GET /conversations`, `GET /conversations/{id}` (`docs/contracts.md`
-> REST API) against the real `mana_leak_core.conversations` service (plan
-> WP5 Notes: the fake conversation service used before WP4 merged is gone).
`api_db` points it at the migrated `mana_leak_test` database."""

from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.anyio


async def test_create_conversation_returns_201_and_persists(
    client: httpx.AsyncClient, api_db: None
) -> None:
    response = await client.post("/conversations", json={"title": "my deck"})

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "my deck"

    detail = await client.get(f"/conversations/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "my deck"
    assert detail.json()["messages"] == []


async def test_create_conversation_without_title_defaults_to_none(
    client: httpx.AsyncClient, api_db: None
) -> None:
    response = await client.post("/conversations", json={})

    assert response.status_code == 201
    assert response.json()["title"] is None


async def test_list_conversations_orders_by_updated_at_descending(
    client: httpx.AsyncClient, api_db: None, fake_gateway
) -> None:
    fake_gateway(deltas=("ok",))
    first = (await client.post("/conversations", json={"title": "first"})).json()
    second = (await client.post("/conversations", json={"title": "second"})).json()

    # Bumping `first`'s `updated_at` with a new message (`append_message`)
    # moves it back to the front of the list (contracts.md -> REST API:
    # `GET /conversations` -> "by `updated_at` desc").
    message_response = await client.post(
        f"/conversations/{first['id']}/messages", json={"content": "hello"}
    )
    assert message_response.status_code == 200

    response = await client.get("/conversations")

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert ids.index(first["id"]) < ids.index(second["id"])


async def test_list_conversations_limit_truncates_results(
    client: httpx.AsyncClient, api_db: None
) -> None:
    for index in range(3):
        await client.post("/conversations", json={"title": f"conv {index}"})

    response = await client.get("/conversations", params={"limit": 2})

    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_get_conversation_returns_detail_with_persisted_messages(
    client: httpx.AsyncClient, api_db: None, fake_gateway
) -> None:
    fake_gateway(deltas=("hi there",))
    created = (await client.post("/conversations", json={})).json()

    message_response = await client.post(
        f"/conversations/{created['id']}/messages", json={"content": "hello"}
    )
    assert message_response.status_code == 200

    response = await client.get(f"/conversations/{created['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "hello"  # `conversation.title` from the first user message
    messages = body["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert [m["seq"] for m in messages] == [1, 2]
    assert messages[0]["content"] == "hello"
    assert messages[1]["content"] == "hi there"
    assert messages[1]["route"] == "other"


async def test_get_unknown_conversation_returns_404_not_found(
    client: httpx.AsyncClient, api_db: None
) -> None:
    response = await client.get(f"/conversations/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_get_conversation_with_malformed_id_returns_422_validation_error(
    client: httpx.AsyncClient,
) -> None:
    # FastAPI/Pydantic rejects the malformed UUID path parameter before the
    # route handler runs -- no database involved.
    response = await client.get("/conversations/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
