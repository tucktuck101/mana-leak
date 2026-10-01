"""Streaming events (`docs/contracts.md` -> Streaming events, Turn orchestration).

`TurnResult` is the `other`-route placeholder for M1: every M1 answer has
`result = None` (PRD FR-5; contracts.md -> Turn orchestration step 6). Once a
later milestone adds `cards`/`combos`/`judge` routes, this becomes
`Annotated[CardsResult | CombosResult | Ruling | NeedMoreInformation |
RulesExplanation | RuleText, Field(discriminator="kind")]` and every field
that already references it (`Final.result`, `MessageOut.payload`'s caller
side) picks up the wider type automatically.
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from .enums import Route
from .errors import ErrorInfo

TurnResult = type(None)


class EventBase(BaseModel):
    conversation_id: UUID
    turn_id: UUID


class MessageStart(EventBase):
    type: Literal["message_start"] = "message_start"
    message_id: UUID  # assistant message id


class TextDelta(EventBase):
    type: Literal["text_delta"] = "text_delta"
    delta: str


class ToolStart(EventBase):
    type: Literal["tool_start"] = "tool_start"
    call_id: str
    tool: str
    args: dict[str, Any]


class ToolEnd(EventBase):
    type: Literal["tool_end"] = "tool_end"
    call_id: str
    tool: str
    ok: bool
    summary: str  # e.g. "3 cards", "not_found"


class Final(EventBase):
    type: Literal["final"] = "final"
    route: Route | None
    text: str  # full assistant text
    result: TurnResult | None = None
    error: ErrorInfo | None = None  # refusal/limit info


class ErrorEvent(EventBase):
    type: Literal["error"] = "error"
    error: ErrorInfo


class MessageEnd(EventBase):
    type: Literal["message_end"] = "message_end"


TurnEvent = Annotated[
    MessageStart | TextDelta | ToolStart | ToolEnd | Final | ErrorEvent | MessageEnd,
    Field(discriminator="type"),
]
