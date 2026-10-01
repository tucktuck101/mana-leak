"""`POST /conversations/{id}/messages` (SSE). Uses a small fake `process_turn`
generator, injected through FastAPI dependency overrides (plan -> WP5 Notes)
-- `mana_leak_core.orchestrator` doesn't exist until WP4 merges. The
orchestrator replaces `test_second_concurrent_post_rejected_with_409_conflict`
below with a real two-concurrent-POST test against the merged orchestrator
once WP4 lands (plan -> WP5 checklist); this fake can only exercise the
API's own "await first event before streaming" plumbing, not the real
in-process lock.
"""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
from mana_leak_api.dependencies import get_process_turn
from mana_leak_api.main import app
from mana_leak_core.contracts.enums import Route
from mana_leak_core.contracts.errors import ErrorCode, ManaLeakError
from mana_leak_core.contracts.events import Final, MessageEnd, MessageStart, TurnEvent

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


async def test_normal_turn_streams_message_start_final_message_end(
    client: httpx.AsyncClient,
) -> None:
    conversation_id = uuid4()
    turn_id = uuid4()

    async def fake_process_turn(
        conv_id: UUID,
        user_message: str | None = None,
        forced_route: Route | None = None,
        session_action: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        assert user_message == "hello"
        yield MessageStart(conversation_id=conv_id, turn_id=turn_id, message_id=uuid4())
        yield Final(conversation_id=conv_id, turn_id=turn_id, route=Route.other, text="hi there")
        yield MessageEnd(conversation_id=conv_id, turn_id=turn_id)

    app.dependency_overrides[get_process_turn] = lambda: fake_process_turn

    response = await client.post(
        f"/conversations/{conversation_id}/messages", json={"content": "hello"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    events = _parse_sse(response.text)
    types = [t for t, _ in events]
    assert types == ["message_start", "final", "message_end"]
    assert '"route":"other"' in events[1][1]
    assert '"result":null' in events[1][1]
    assert '"text":"hi there"' in events[1][1]


async def test_conflict_raised_before_first_event_maps_to_409_not_in_stream(
    client: httpx.AsyncClient,
) -> None:
    async def fake_process_turn(
        conv_id: UUID,
        user_message: str | None = None,
        forced_route: Route | None = None,
        session_action: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        raise ManaLeakError(ErrorCode.conflict, "turn already in flight")
        yield  # pragma: no cover -- makes this an async generator function

    app.dependency_overrides[get_process_turn] = lambda: fake_process_turn

    response = await client.post(f"/conversations/{uuid4()}/messages", json={"content": "hello"})

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "conflict"
    # A pre-stream failure is a plain JSON error body, not `text/event-stream`.
    assert "text/event-stream" not in response.headers["content-type"]


async def test_second_concurrent_post_rejected_with_409_conflict(
    client: httpx.AsyncClient,
) -> None:
    """Simulates the per-conversation in-process lock with a fake generator
    (shared contracts -> `orchestrator.py`: "raises `ManaLeakError(conflict)`
    on the generator's first `__anext__()`, before anything is emitted").
    Replaced with a real test against the merged orchestrator once WP4 lands
    (plan -> WP5 checklist)."""
    conversation_id = uuid4()
    in_flight: set[UUID] = set()
    first_turn_started = asyncio.Event()
    release_first_turn = asyncio.Event()

    async def fake_process_turn(
        conv_id: UUID,
        user_message: str | None = None,
        forced_route: Route | None = None,
        session_action: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        if conv_id in in_flight:
            raise ManaLeakError(ErrorCode.conflict, "turn already in flight")
        in_flight.add(conv_id)
        first_turn_started.set()
        try:
            turn_id = uuid4()
            yield MessageStart(conversation_id=conv_id, turn_id=turn_id, message_id=uuid4())
            await release_first_turn.wait()
            yield Final(conversation_id=conv_id, turn_id=turn_id, route=Route.other, text="ok")
            yield MessageEnd(conversation_id=conv_id, turn_id=turn_id)
        finally:
            in_flight.discard(conv_id)

    app.dependency_overrides[get_process_turn] = lambda: fake_process_turn

    first_request = asyncio.ensure_future(
        client.post(f"/conversations/{conversation_id}/messages", json={"content": "first"})
    )
    await first_turn_started.wait()

    second_response = await client.post(
        f"/conversations/{conversation_id}/messages", json={"content": "second"}
    )
    assert second_response.status_code == 409
    assert second_response.json()["error"]["code"] == "conflict"

    release_first_turn.set()
    first_response = await first_request
    assert first_response.status_code == 200
    assert [t for t, _ in _parse_sse(first_response.text)] == [
        "message_start",
        "final",
        "message_end",
    ]


async def test_malformed_action_value_returns_422_validation_error(
    client: httpx.AsyncClient,
) -> None:
    # FastAPI resolves this route's `Depends()` alongside body parsing,
    # before raising the validation error, so it must be overridden even
    # though an invalid `action` literal never reaches the handler body.
    async def unreachable_process_turn(
        conv_id: UUID,
        user_message: str | None = None,
        forced_route: Route | None = None,
        session_action: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        raise AssertionError("should not be called: body validation fails first")
        yield  # pragma: no cover

    app.dependency_overrides[get_process_turn] = lambda: unreachable_process_turn

    response = await client.post(
        f"/conversations/{uuid4()}/messages", json={"action": "not-a-real-action"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
