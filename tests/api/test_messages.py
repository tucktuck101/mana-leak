"""`POST /conversations/{id}/messages` (SSE) against the real
`mana_leak_core.orchestrator.process_turn` and conversation service
(plan -> WP5 Notes: the fake generator used before WP4 merged is gone).
`api_db` points both at the migrated `mana_leak_test` database; `fake_gateway`
stands in for the model gateway (WP3's own contract) so turn timing/content
is deterministic without a live network call. The end-to-end test at the
bottom is the one exception (`@pytest.mark.live`): it also exercises the
real gateway/`CHAT_MODEL` call.
"""

import asyncio
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.anyio


def _parse_sse(raw: str) -> list[tuple[str, str]]:
    """`event: <type>\\ndata: <json>\\n\\n` -> `[(type, data_json), ...]`,
    skipping `: ping` comments (contracts.md -> SSE mapping)."""
    events: list[tuple[str, str]] = []
    event_type: str | None = None
    for line in raw.split("\n"):
        if line.startswith(":"):
            continue
        if line.startswith("event: "):
            event_type = line[len("event: ") :]
        elif line.startswith("data: ") and event_type is not None:
            events.append((event_type, line[len("data: ") :]))
            event_type = None
    return events


async def test_normal_turn_streams_message_start_deltas_final_message_end(
    client: httpx.AsyncClient, api_db: None, fake_gateway
) -> None:
    fake_gateway(deltas=("Mana ", "Leak."))

    created = (await client.post("/conversations", json={})).json()

    response = await client.post(
        f"/conversations/{created['id']}/messages", json={"content": "hello"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    events = _parse_sse(response.text)
    types = [t for t, _ in events]
    # contracts.md -> Streaming events: `message_start` -> `text_delta`* ->
    # `final` -> `message_end`.
    assert types == ["message_start", "text_delta", "text_delta", "final", "message_end"]
    assert '"route":"other"' in events[-2][1]
    assert '"result":null' in events[-2][1]
    assert '"text":"Mana Leak."' in events[-2][1]


async def test_second_concurrent_post_rejected_with_409_conflict(
    client: httpx.AsyncClient, api_db: None, fake_gateway
) -> None:
    """AC-5, FR-6, through two real HTTP requests against the real
    `process_turn`: the first turn's (faked) gateway call hangs until
    released, holding the real per-conversation in-process lock while the
    second request runs concurrently on the same event loop. A conflict
    raised on `process_turn`'s first `__anext__()` never becomes an
    in-stream `error` event (`contracts.md` -> SSE mapping) -- it is a plain
    JSON error body with no `text/event-stream` content type."""
    fake = fake_gateway(deltas=("partial",), hang_after_deltas=True)

    created = (await client.post("/conversations", json={})).json()
    conversation_id = created["id"]

    first_request = asyncio.ensure_future(
        client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "first"},
            timeout=10,
        )
    )
    # The fake gateway is only reached after `process_turn` has persisted
    # the user message, yielded `message_start`, and acquired the
    # conversation lock (orchestrator.py's `_run_turn`): by the time
    # `fake.called` is set, the second request below is guaranteed to
    # observe the lock held.
    await asyncio.wait_for(fake.called.wait(), timeout=5)

    second_response = await client.post(
        f"/conversations/{conversation_id}/messages", json={"content": "second"}, timeout=10
    )
    assert second_response.status_code == 409
    assert second_response.json()["error"]["code"] == "conflict"
    assert "text/event-stream" not in second_response.headers["content-type"]

    fake.release.set()
    first_response = await asyncio.wait_for(first_request, timeout=10)
    assert first_response.status_code == 200
    assert [t for t, _ in _parse_sse(first_response.text)] == [
        "message_start",
        "text_delta",
        "final",
        "message_end",
    ]


async def test_malformed_action_value_returns_422_validation_error(
    client: httpx.AsyncClient,
) -> None:
    # Pydantic rejects the body's `action` literal before the route handler
    # (or any dependency it needs) ever runs -- no database involved.
    response = await client.post(
        f"/conversations/{uuid4()}/messages", json={"action": "not-a-real-action"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --- Live end-to-end (AC-1, AC-2; CHAT_MODEL) --------------------------------


@pytest.mark.live
async def test_live_create_conversation_post_message_and_read_history(
    client: httpx.AsyncClient, api_db: None
) -> None:
    """The full API surface against the real gateway/`CHAT_MODEL`: create a
    conversation, post a message, read the SSE events in contract order
    (`message_start` -> `text_delta`* -> `final` -> `message_end`), then
    confirm `GET /conversations/{id}` shows both the user and assistant
    messages (AC-1, AC-2)."""
    create_response = await client.post("/conversations", json={"title": "live e2e"})
    assert create_response.status_code == 201
    conversation_id = create_response.json()["id"]

    user_content = "Reply with a single short sentence."
    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": user_content},
        timeout=60,
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    events = _parse_sse(response.text)
    types = [t for t, _ in events]
    assert types[0] == "message_start"
    assert types[-2:] == ["final", "message_end"]
    assert all(t == "text_delta" for t in types[1:-2])  # contract order's `text_delta*`

    final_body = events[-2][1]
    assert '"route":"other"' in final_body
    assert '"result":null' in final_body

    history_response = await client.get(f"/conversations/{conversation_id}")
    assert history_response.status_code == 200
    messages = history_response.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == user_content
    assert messages[1]["content"]  # non-empty assistant answer, persisted before `final`
