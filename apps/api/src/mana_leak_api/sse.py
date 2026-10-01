"""SSE encoding (`docs/contracts.md` -> Streaming events -> SSE mapping).

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache

event: <TurnEvent.type>
data: <TurnEvent JSON on one line>

```

Plus a `: ping` comment every 15 s while waiting on the next event. The
stream closes after `message_end` (the source generator's `StopAsyncIteration`).

**One producer task.** `sse_stream` drives the source generator from a
single long-lived task that feeds an `asyncio.Queue`, and does the 15 s ping
wait on the queue -- never on the generator. A task per `__anext__()` would
give every event its own *copy* of the context (`contextvars` are copied at
task creation and never propagate back), so `process_turn`'s per-turn
model-call counter would reset on every event and the gateway's model-call
cap would be unenforceable; and any `asyncio.timeout` the core opens would
be armed in a task that is already done by the time it should fire, so a
stall between two chunks would never be cancelled. Both are invisible with
M1's single short model call and silently wrong as soon as a turn makes
several (M3's tool loop).

`sse_stream` always closes the source generator (`contextlib.aclosing`) when
it itself is closed, cancelled, or exhausted -- normal completion, an
unhandled exception, or the ASGI server closing the response body iterator
on client disconnect (AC-10). It first cancels the producer task, which
throws `CancelledError` into `process_turn` at its current suspension point;
that is how the orchestrator's per-conversation lock release and partial-text
persistence (shared contracts -> `orchestrator.py`, WP4) get triggered.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator

from mana_leak_core.contracts.events import TurnEvent

SSE_MEDIA_TYPE = "text/event-stream; charset=utf-8"
PING_INTERVAL_S = 15.0

_PING = b": ping\n\n"

#: Queue payloads: the next event, the failure that ended the generator, or
#: `None` for a clean `StopAsyncIteration`.
type _Item = TurnEvent | Exception | None


def _encode(event: TurnEvent) -> bytes:
    return f"event: {event.type}\ndata: {event.model_dump_json()}\n\n".encode()


async def _produce(events: AsyncIterator[TurnEvent], queue: asyncio.Queue[_Item]) -> None:
    """Iterates the whole turn in one task (see the module docstring). The
    queue holds at most one event, so the source generator stays suspended
    at its `yield` until the client has taken the previous one -- the
    backpressure a disconnect relies on."""
    try:
        async for event in events:
            await queue.put(event)
    except Exception as exc:  # re-raised on the consumer side, in order
        await queue.put(exc)
    else:
        await queue.put(None)


async def sse_stream(
    events: AsyncIterator[TurnEvent], first_event: TurnEvent
) -> AsyncIterator[bytes]:
    """Encodes `first_event` (already pulled from `events` by the caller, so
    a `ManaLeakError` raised on the first `__anext__()` -- e.g. `conflict`
    from the per-conversation lock -- maps to an HTTP error instead of an
    in-stream `error` event) followed by the rest of `events`."""
    async with contextlib.aclosing(events):
        yield _encode(first_event)
        queue: asyncio.Queue[_Item] = asyncio.Queue(maxsize=1)
        producer = asyncio.create_task(_produce(events, queue))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), PING_INTERVAL_S)
                except TimeoutError:
                    yield _PING
                    continue
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                yield _encode(item)
        finally:
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer
