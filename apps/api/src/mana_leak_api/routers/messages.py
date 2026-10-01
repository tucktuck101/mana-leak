"""`POST /conversations/{conversation_id}/messages` (`docs/contracts.md` ->
REST API, Streaming events).

Awaits the first SSE-encoded chunk from `sse.sse_stream` before constructing
the streaming response (shared contracts -> SSE encoding, WP6 F1): a
`ManaLeakError` raised there -- in M1, `conflict` from the per-conversation
in-process lock (AC-5) -- propagates out of this coroutine and is mapped by
`errors.py`'s `ManaLeakError` handler to its HTTP status, never an in-stream
`error` event (contracts.md -> SSE mapping). `sse_stream` itself, not this
router, drives `process_turn`'s generator from its very first `__anext__()`
(F1 -- the producer task starts before any event is pulled, so the whole
turn, including its first event, runs in one task).
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from mana_leak_core.contracts.enums import SessionControl
from pydantic import BaseModel, ConfigDict

from mana_leak_api.dependencies import ProcessTurn, get_process_turn
from mana_leak_api.sse import SSE_MEDIA_TYPE, resume_stream, sse_stream

router = APIRouter(prefix="/conversations", tags=["messages"])


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    action: SessionControl | None = None


@router.post("/{conversation_id}/messages")
async def post_message(
    conversation_id: UUID,
    body: MessageCreate = MessageCreate(),  # noqa: B008
    process_turn: ProcessTurn = Depends(get_process_turn),  # noqa: B008
) -> StreamingResponse:
    agen = process_turn(
        conversation_id,
        user_message=body.content,
        session_action=body.action,
    )
    stream = sse_stream(agen)
    headers = {"Cache-Control": "no-cache"}
    try:
        first_chunk = await anext(stream)
    except StopAsyncIteration:
        # Defensive: `process_turn` always yields at least `message_start`
        # today, but a generator that produces zero events is still a
        # well-formed empty stream, not an error.
        return StreamingResponse((), media_type=SSE_MEDIA_TYPE, headers=headers)
    return StreamingResponse(
        resume_stream(first_chunk, stream), media_type=SSE_MEDIA_TYPE, headers=headers
    )
