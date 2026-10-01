"""`mana_leak_api.sse.sse_stream` (`docs/contracts.md` -> Streaming events ->
SSE mapping): encoding, the 15 s `: ping` comment, and closing the source
generator on exit (AC-10's API-side plumbing -- a real client-disconnect
test against a live socket is WP8's e2e concern)."""

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from mana_leak_api import sse as sse_module
from mana_leak_api.sse import sse_stream
from mana_leak_core.contracts.enums import Route
from mana_leak_core.contracts.events import Final, MessageEnd, MessageStart, TurnEvent

pytestmark = pytest.mark.anyio


def _message_start() -> MessageStart:
    return MessageStart(conversation_id=uuid4(), turn_id=uuid4(), message_id=uuid4())


async def test_encodes_event_type_and_json_line() -> None:
    first = _message_start()

    async def events() -> AsyncIterator[TurnEvent]:
        yield Final(
            conversation_id=first.conversation_id,
            turn_id=first.turn_id,
            route=Route.other,
            text="hi",
        )
        yield MessageEnd(conversation_id=first.conversation_id, turn_id=first.turn_id)

    chunks = [chunk async for chunk in sse_stream(events(), first)]

    assert chunks[0].startswith(b"event: message_start\ndata: ")
    assert chunks[0].endswith(b"\n\n")
    assert chunks[1].startswith(b"event: final\ndata: ")
    assert chunks[2].startswith(b"event: message_end\ndata: ")
    assert str(first.conversation_id).encode() in chunks[2]


async def test_stream_ends_after_message_end() -> None:
    first = _message_start()

    async def events() -> AsyncIterator[TurnEvent]:
        yield MessageEnd(conversation_id=first.conversation_id, turn_id=first.turn_id)

    chunks = [chunk async for chunk in sse_stream(events(), first)]
    assert len(chunks) == 2  # message_start (passed in) + message_end


async def test_ping_emitted_while_waiting_past_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sse_module, "PING_INTERVAL_S", 0.05)
    first = _message_start()
    release = asyncio.Event()

    async def events() -> AsyncIterator[TurnEvent]:
        await release.wait()
        yield MessageEnd(conversation_id=first.conversation_id, turn_id=first.turn_id)

    gen = sse_stream(events(), first)
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
    release, partial-text persistence) gets to run on a client disconnect."""
    monkeypatch.setattr(sse_module, "PING_INTERVAL_S", 0.01)
    first = _message_start()
    cleaned_up = False
    parked = asyncio.Event()

    async def events() -> AsyncIterator[TurnEvent]:
        nonlocal cleaned_up
        try:
            await parked.wait()  # parks forever until the generator is closed
            yield MessageEnd(conversation_id=first.conversation_id, turn_id=first.turn_id)
        finally:
            cleaned_up = True

    gen = sse_stream(events(), first)
    await anext(gen)  # consume the first (already-pulled) event
    # Ping lands first (parked never resolves): `events` is now genuinely
    # suspended mid-body, inside its own try/finally, not merely uncreated.
    assert await anext(gen) == b": ping\n\n"

    await gen.aclose()

    assert cleaned_up is True
