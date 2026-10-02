"""SSE encoding (`docs/contracts.md` -> Streaming events -> SSE mapping).

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache

event: <TurnEvent.type>
data: <TurnEvent JSON on one line>

```

Plus a `: ping` comment every 15 s while waiting on the next event. The
stream closes after `message_end` (the source generator's `StopAsyncIteration`).

**One producer task, from the very first event (M2 plan F1).** `sse_stream`
drives the source generator (`process_turn`) from a single long-lived task
that feeds an `asyncio.Queue`, starting with that task's own first
`__anext__()` -- nothing else, including the caller, ever calls
`events.__anext__()` directly. A task per `__anext__()`, or a first
`__anext__()` pulled by the caller before the producer task exists, would
give that event its own *copy* of the context (`contextvars` are copied at
task creation and never propagate back): `process_turn`'s per-turn
model-call counter would reset, the gateway's model-call cap would be
unenforceable, and (M2) a Langfuse span opened at the top of `_run_turn`'s
body and closed at its end would be entered in one task and exited in
another, which OpenTelemetry logs as a context-detach error on every turn.
Both are invisible with M1's single short model call and silently wrong as
soon as a turn makes several (M3's tool loop) or opens a span across the
whole turn (M2).

`routers/messages.py` pulls the first *encoded* chunk itself with
`await anext(stream)`, sourced from the same queue the producer task
already started filling, so a `ManaLeakError` raised on `process_turn`'s
first `__anext__()` (e.g. `conflict` from the per-conversation lock) still
propagates out of that call -- now from inside the producer task, through
the queue, instead of directly -- and still maps to an HTTP error rather
than an in-stream `error` event.

The source generator is always closed, and always **by the producer task**
(`_produce` owns the `contextlib.aclosing`), whether the stream completes,
fails, or is abandoned -- including when the ASGI server drops the response
body iterator on a client disconnect (AC-10). `sse_stream`'s `finally`
cancels the producer first, which throws `CancelledError` into
`process_turn` at its current suspension point; that is how the
orchestrator's per-conversation lock release and partial-text persistence
(shared contracts -> `orchestrator.py`, WP4/WP5) get triggered. If the
producer was parked at `queue.put` (the common disconnect case: the client
stopped reading, so the queue is full) that cancellation lands in the
producer, *not* in the suspended generator, so the generator still has to
be closed explicitly -- and closing it from any other task would run
`_run_turn`'s `with start_turn_trace(...)` exit in a task that never
entered it, which OpenTelemetry logs as `Failed to detach context` once per
aborted turn (AC-4). Hence the `aclosing` lives inside `_produce`;
`sse_stream` keeps an outer one as a no-op fallback for the case where the
producer task never started.
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
    """Iterates the whole turn in one task, from its very first `__anext__()`
    to its `aclose()` (see the module docstring). The queue holds at most one
    event, so the source generator stays suspended at its `yield` until the
    client has taken the previous one -- the backpressure a disconnect relies
    on -- and a cancellation that lands here rather than inside the generator
    (parked at `queue.put`) still finalises the generator in this task."""
    try:
        async with contextlib.aclosing(events):
            async for event in events:
                await queue.put(event)
    except Exception as exc:  # re-raised on the consumer side, in order
        await queue.put(exc)
    else:
        await queue.put(None)


async def sse_stream(events: AsyncIterator[TurnEvent]) -> AsyncIterator[bytes]:
    """Starts the one producer task first, then encodes whatever it
    produces -- including the first event -- as SSE chunks. Raises an
    `Exception` the source generator raised (on its first `__anext__()` or
    any later one) instead of encoding it, in order, so the caller's own
    `await anext(stream)` surfaces a first-event failure exactly as a
    direct `await anext(events)` used to."""
    # Fallback only: `_produce` owns closing `events` (module docstring), and
    # a second `aclose()` on an already-closed generator does nothing. This
    # covers the one case the producer cannot -- never having started.
    async with contextlib.aclosing(events):
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


async def resume_stream(first_chunk: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """`StreamingResponse`'s body: the chunk `routers/messages.py` already
    pulled from `rest` (to decide whether to raise it as an HTTP error
    instead) re-attached as the first item, followed by the remainder of
    the same `sse_stream` generator -- not a second one, so there is still
    exactly one producer task per turn."""
    yield first_chunk
    async for chunk in rest:
        yield chunk
