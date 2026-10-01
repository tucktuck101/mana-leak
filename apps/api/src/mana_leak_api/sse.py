"""SSE encoding (`docs/contracts.md` -> Streaming events -> SSE mapping).

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache

event: <TurnEvent.type>
data: <TurnEvent JSON on one line>

```

Plus a `: ping` comment every 15 s while waiting on the next event. The
stream closes after `message_end` (the source generator's `StopAsyncIteration`).

`sse_stream` always closes the source generator (`contextlib.aclosing`) when
it itself is closed, cancelled, or exhausted -- normal completion, an
unhandled exception, or the ASGI server closing the response body iterator
on client disconnect (AC-10). Closing the source generator throws
`GeneratorExit` into `process_turn` at its current suspension point, which
is how the orchestrator's per-conversation lock release and partial-text
persistence (shared contracts -> `orchestrator.py`, WP4) get triggered.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator

from mana_leak_core.contracts.events import TurnEvent

SSE_MEDIA_TYPE = "text/event-stream; charset=utf-8"
PING_INTERVAL_S = 15.0

_PING = b": ping\n\n"


def _encode(event: TurnEvent) -> bytes:
    return f"event: {event.type}\ndata: {event.model_dump_json()}\n\n".encode()


async def sse_stream(
    events: AsyncIterator[TurnEvent], first_event: TurnEvent
) -> AsyncIterator[bytes]:
    """Encodes `first_event` (already pulled from `events` by the caller, so
    a `ManaLeakError` raised on the first `__anext__()` -- e.g. `conflict`
    from the per-conversation lock -- maps to an HTTP error instead of an
    in-stream `error` event) followed by the rest of `events`."""
    async with contextlib.aclosing(events):
        yield _encode(first_event)
        pending: asyncio.Task[TurnEvent] | None = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(anext(events))
                done, _pending_set = await asyncio.wait({pending}, timeout=PING_INTERVAL_S)
                if not done:
                    yield _PING
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    return
                finally:
                    pending = None
                yield _encode(event)
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                with contextlib.suppress(BaseException):
                    await pending
