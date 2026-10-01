"""`POST /conversations/{id}/messages` (SSE) against the real
`mana_leak_core.orchestrator.process_turn` and conversation service
(plan -> WP5 Notes: the fake generator used before WP4 merged is gone).
`api_db` points both at the migrated `mana_leak_test` database; `fake_gateway`
stands in for the model gateway (WP3's own contract) so turn timing/content
is deterministic without a live network call -- except where the gateway's
own limits are what is under test, which run the real gateway over a fake
LiteLLM. The end-to-end test at the bottom is the one exception
(`@pytest.mark.live`): it also exercises the real gateway/`CHAT_MODEL` call.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

import httpx
import litellm
import pytest
from mana_leak_api.dependencies import ProcessTurn, get_process_turn
from mana_leak_api.main import app
from mana_leak_core import gateway
from mana_leak_core.contracts.enums import AuditEventType
from mana_leak_core.contracts.errors import ErrorCode, ManaLeakError
from mana_leak_core.contracts.events import MessageEnd, MessageStart, TurnEvent
from mana_leak_core.db.models import AuditEvent
from mana_leak_core.settings import Settings, get_settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio


def _gateway_settings(**overrides: object) -> Settings:
    """Real `Settings` with `overrides` applied, for patching the gateway's
    own `get_settings` (its per-call limits)."""
    base = get_settings()
    return Settings(
        **{**base.model_dump(), "openrouter_api_key": base.openrouter_api_key},
    ).model_copy(update=overrides)


def _chunk(delta: str, finish_reason: str | None = None) -> SimpleNamespace:
    """One LiteLLM streaming chunk, as `gateway._stream` reads it."""
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=delta), finish_reason=finish_reason)]
    )


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


async def test_a_pre_stream_model_limit_exceeded_maps_to_an_error_response(
    client: httpx.AsyncClient,
) -> None:
    """`model_limit_exceeded` has no row in contracts.md's HTTP error mapping
    because the cap can only be hit after `message_start`, so a client
    normally meets it as an in-stream `error` event. Raised before the first
    event (a later milestone's `complete()` call outside a turn stream), the
    handler must still answer with an `ErrorResponse` body carrying the real
    code -- never FastAPI's default plain-text 500."""

    def _override() -> ProcessTurn:
        async def process_turn(*args: object, **kwargs: object) -> AsyncIterator[TurnEvent]:
            raise ManaLeakError(
                ErrorCode.model_limit_exceeded,
                "model-call budget of 8 calls per turn exceeded",
                details={"limit": "model_calls_max", "value": 8},
            )
            yield  # pragma: no cover - unreachable; makes this a generator

        return process_turn

    app.dependency_overrides[get_process_turn] = _override

    response = await client.post(f"/conversations/{uuid4()}/messages", json={"content": "hi"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "model_limit_exceeded",
            "message": "model-call budget of 8 calls per turn exceeded",
            "retryable": False,
            "details": {"limit": "model_calls_max", "value": 8},
        }
    }


async def test_the_whole_turn_runs_in_one_task_including_the_first_event(
    client: httpx.AsyncClient,
) -> None:
    """F1 (`sse.py`): the SSE producer task must drive `process_turn` from
    its very first `__anext__()` -- the request-handling task itself must
    never call it -- so a value threaded through the generator's own call
    stack (WP5's turn-level Langfuse span, the gateway's model-call-budget
    contextvar) survives every event, including the first one, not only the
    ones after it."""
    tasks: list[int | None] = []

    def _override() -> ProcessTurn:
        async def process_turn(*args: object, **kwargs: object) -> AsyncIterator[TurnEvent]:
            conversation_id, turn_id = uuid4(), uuid4()
            tasks.append(id(asyncio.current_task()))
            yield MessageStart(conversation_id=conversation_id, turn_id=turn_id, message_id=uuid4())
            tasks.append(id(asyncio.current_task()))
            yield MessageEnd(conversation_id=conversation_id, turn_id=turn_id)
            tasks.append(id(asyncio.current_task()))

        return process_turn

    app.dependency_overrides[get_process_turn] = _override

    response = await client.post(f"/conversations/{uuid4()}/messages", json={"content": "hi"})

    assert response.status_code == 200
    assert len(tasks) == 3
    assert len(set(tasks)) == 1  # one task drove every __anext__(), including the first


async def test_a_mid_stream_stall_ends_the_turn_with_the_model_call_timeout(
    client: httpx.AsyncClient,
    db: AsyncSession,
    api_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-6/NFR-1 on the deployed path, through the real gateway: a stream
    that starts promptly and then stalls must be cancelled by the gateway's
    own `model_call_timeout_s`, not left to the 120 s turn deadline and not
    reported as a client disconnect. This holds only because the gateway
    bounds each chunk individually: a timeout scope held open across a
    `yield` is armed in whichever task resumes the generator, never in the
    one that is stalled waiting for the next chunk."""
    monkeypatch.setattr(gateway, "get_settings", lambda: _gateway_settings(model_call_timeout_s=1))

    async def acompletion(**kwargs: object) -> AsyncIterator[SimpleNamespace]:
        async def stream() -> AsyncIterator[SimpleNamespace]:
            yield _chunk("Mana ")
            await asyncio.sleep(5)  # longer than `model_call_timeout_s`
            yield _chunk("", finish_reason="stop")

        return stream()

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    created = (await client.post("/conversations", json={})).json()

    response = await client.post(
        f"/conversations/{created['id']}/messages", json={"content": "hello"}
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [t for t, _ in events] == ["message_start", "text_delta", "error", "message_end"]
    error = json.loads(dict(events)["error"])["error"]
    assert error["code"] == "timeout"
    assert error["message"] == "model call timed out"  # not "client disconnected"
    assert error["details"] == {"limit": "model_call_timeout_s", "value": 1}

    # The partial answer is persisted, and the limit is attributable to the turn.
    turn_id = json.loads(events[0][1])["turn_id"]
    rows = (
        (await db.execute(select(AuditEvent).where(AuditEvent.turn_id == uuid.UUID(turn_id))))
        .scalars()
        .all()
    )
    assert [row.event_type for row in rows] == [AuditEventType.limit_reached.value]
    assert str(rows[0].conversation_id) == created["id"]
    assert rows[0].details == {"limit": "model_call_timeout_s", "value": 1}

    messages = (await client.get(f"/conversations/{created['id']}")).json()["messages"]
    assert messages[-1]["content"] == "Mana "


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
