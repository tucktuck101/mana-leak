"""`mana_leak_api.sse.sse_stream` (`docs/contracts.md` -> Streaming events ->
SSE mapping): encoding, the 15 s `: ping` comment, closing the source
generator on exit (AC-10's API-side plumbing -- a real client-disconnect
test against a live socket is WP8's e2e concern), and the single producer
task -- now started before the first event is ever pulled (M2 plan F1) --
the turn's `contextvars` and timeouts (and, from M2, a turn-spanning
Langfuse observation) depend on."""

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import litellm
import pytest
from mana_leak_api import sse as sse_module
from mana_leak_api.sse import sse_stream
from mana_leak_core import gateway
from mana_leak_core.contracts.enums import Route
from mana_leak_core.contracts.errors import ErrorCode, ManaLeakError
from mana_leak_core.contracts.events import (
    Final,
    MessageEnd,
    MessageStart,
    TextDelta,
    TurnEvent,
)

pytestmark = pytest.mark.anyio


def _message_start() -> MessageStart:
    return MessageStart(conversation_id=uuid4(), turn_id=uuid4(), message_id=uuid4())


async def test_encodes_event_type_and_json_line() -> None:
    first = _message_start()

    async def events() -> AsyncIterator[TurnEvent]:
        yield first
        yield Final(
            conversation_id=first.conversation_id,
            turn_id=first.turn_id,
            route=Route.other,
            text="hi",
        )
        yield MessageEnd(conversation_id=first.conversation_id, turn_id=first.turn_id)

    chunks = [chunk async for chunk in sse_stream(events())]

    assert chunks[0].startswith(b"event: message_start\ndata: ")
    assert chunks[0].endswith(b"\n\n")
    assert chunks[1].startswith(b"event: final\ndata: ")
    assert chunks[2].startswith(b"event: message_end\ndata: ")
    assert str(first.conversation_id).encode() in chunks[2]


async def test_stream_ends_after_message_end() -> None:
    first = _message_start()

    async def events() -> AsyncIterator[TurnEvent]:
        yield first
        yield MessageEnd(conversation_id=first.conversation_id, turn_id=first.turn_id)

    chunks = [chunk async for chunk in sse_stream(events())]
    assert len(chunks) == 2  # message_start + message_end


async def test_ping_emitted_while_waiting_past_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sse_module, "PING_INTERVAL_S", 0.05)
    first = _message_start()
    release = asyncio.Event()

    async def events() -> AsyncIterator[TurnEvent]:
        yield first
        await release.wait()
        yield MessageEnd(conversation_id=first.conversation_id, turn_id=first.turn_id)

    gen = sse_stream(events())
    first_chunk = await anext(gen)
    assert first_chunk.startswith(b"event: message_start\ndata: ")

    assert await anext(gen) == b": ping\n\n"

    release.set()
    assert (await anext(gen)).startswith(b"event: message_end\ndata: ")
    with pytest.raises(StopAsyncIteration):
        await anext(gen)


async def test_closing_the_stream_closes_the_source_generator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the plumbing `sse_stream` relies on for AC-10: closing (or
    abandoning) the outer byte stream must `aclose()` the inner `TurnEvent`
    generator, which is how `process_turn`'s `finally` (per-conversation lock
    release, partial-text persistence) gets to run on a client disconnect.

    `events` parks *before* yielding anything -- including its first event
    -- so the first chunk off `gen` is necessarily a ping, not a real event:
    proof that the producer task (not the caller) is the one blocked waiting
    on `events`' own first `__anext__()` (F1), not merely on a later one."""
    monkeypatch.setattr(sse_module, "PING_INTERVAL_S", 0.01)
    cleaned_up = False
    parked = asyncio.Event()

    async def events() -> AsyncIterator[TurnEvent]:
        nonlocal cleaned_up
        try:
            await parked.wait()  # parks forever until the generator is closed
            yield _message_start()
        finally:
            cleaned_up = True

    gen = sse_stream(events())
    assert await anext(gen) == b": ping\n\n"

    await gen.aclose()

    assert cleaned_up is True


# --- One producer task per turn, from the first event (F1) -------------------


async def test_the_producer_task_drives_the_first_anext_too() -> None:
    """The bug F1 fixes: before, the caller's own task ran `events`'
    first `__anext__()` directly, and only the producer task (created
    afterwards) ran the rest -- two different tasks driving one generator.
    Proves the fix: exactly one task, the producer's, runs `events`' whole
    body from its first suspension point through its last."""
    tasks: list[asyncio.Task[None] | None] = []

    async def events() -> AsyncIterator[TurnEvent]:
        tasks.append(asyncio.current_task())
        yield _message_start()
        tasks.append(asyncio.current_task())

    gen = sse_stream(events())
    await anext(gen)
    with pytest.raises(StopAsyncIteration):
        await anext(gen)

    assert len(tasks) == 2
    assert tasks[0] is tasks[1]
    assert tasks[0] is not asyncio.current_task()


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The *real* gateway over a fake LiteLLM, so `complete()` enforces the
    per-turn budget for real without a network call. Audit writes go to a
    mock: this test needs no database."""

    async def acompletion(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok"),
                    finish_reason="stop",
                )
            ]
        )

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    monkeypatch.setattr(gateway, "emit_audit_event", AsyncMock())


async def test_model_call_budget_is_enforced_across_the_whole_stream(fake_model: None) -> None:
    """The turn's `contextvars` -- here the model-call counter -- must survive
    from the first event to the last. `start_turn_budget` is the generator's
    very own first statement, run entirely inside the producer task (F1), so
    there is no longer a second, external `anext()` that could set it in a
    different task from the one that later reads/increments it."""
    conversation_id, turn_id = uuid4(), uuid4()
    outcomes: list[object] = []

    async def events() -> AsyncIterator[TurnEvent]:
        gateway.start_turn_budget(max_calls=2)
        yield MessageStart(conversation_id=conversation_id, turn_id=turn_id, message_id=uuid4())
        for _ in range(3):
            try:
                await gateway.complete([{"role": "user", "content": "hi"}], model="m")
                outcomes.append("ok")
            except ManaLeakError as exc:
                outcomes.append(exc.code)
            yield TextDelta(conversation_id=conversation_id, turn_id=turn_id, delta="x")
        yield MessageEnd(conversation_id=conversation_id, turn_id=turn_id)

    chunks = [chunk async for chunk in sse_stream(events())]

    assert outcomes == ["ok", "ok", ErrorCode.model_limit_exceeded]
    assert len(chunks) == 5  # message_start + 3 deltas + message_end


async def test_an_exception_after_the_first_event_propagates_out_of_the_stream() -> None:
    """The producer swallows nothing: a failure raised by the source
    generator mid-turn reaches the consumer, in order, instead of ending the
    byte stream as if the turn had finished normally."""
    first = _message_start()

    async def events() -> AsyncIterator[TurnEvent]:
        yield first
        yield TextDelta(
            conversation_id=first.conversation_id, turn_id=first.turn_id, delta="partial"
        )
        raise ManaLeakError(ErrorCode.internal_error, "turn blew up")

    gen = sse_stream(events())
    assert (await anext(gen)).startswith(b"event: message_start\ndata: ")
    assert (await anext(gen)).startswith(b"event: text_delta\ndata: ")
    with pytest.raises(ManaLeakError) as excinfo:
        await anext(gen)
    assert excinfo.value.message == "turn blew up"


async def test_an_exception_on_the_first_event_propagates_out_of_the_stream() -> None:
    """F1's actual fix target: a failure raised by `events` before its first
    yield (e.g. `conflict` from the per-conversation lock, `orchestrator.py`)
    must still propagate out of the *caller's* `await anext(stream)` --
    unchanged from the old direct-`anext(events)` behaviour -- even though
    it is now the producer task, not the caller, that runs that first
    `__anext__()`."""

    async def events() -> AsyncIterator[TurnEvent]:
        raise ManaLeakError(ErrorCode.conflict, "a turn is already in flight")
        yield _message_start()  # pragma: no cover - unreachable; makes this a generator

    gen = sse_stream(events())
    with pytest.raises(ManaLeakError) as excinfo:
        await anext(gen)
    assert excinfo.value.code == ErrorCode.conflict
