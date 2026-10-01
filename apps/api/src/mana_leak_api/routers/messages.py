"""`POST /conversations/{conversation_id}/messages` (`docs/contracts.md` ->
REST API, Streaming events).

Awaits `process_turn`'s first event before constructing the streaming
response (shared contracts -> SSE encoding, WP5 row): a `ManaLeakError`
raised there -- in M1, `conflict` from the per-conversation in-process lock
(AC-5) -- propagates out of this coroutine and is mapped by `errors.py`'s
`ManaLeakError` handler to its HTTP status, never an in-stream `error`
event (contracts.md -> SSE mapping).
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from mana_leak_api.dependencies import ProcessTurn, get_process_turn
from mana_leak_api.sse import SSE_MEDIA_TYPE, sse_stream

router = APIRouter(prefix="/conversations", tags=["messages"])


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    action: Literal["answer", "new_question", "end_session"] | None = None


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
    first_event = await anext(agen)
    return StreamingResponse(
        sse_stream(agen, first_event),
        media_type=SSE_MEDIA_TYPE,
        headers={"Cache-Control": "no-cache"},
    )
