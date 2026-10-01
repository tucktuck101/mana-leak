"""Core service interfaces, REST API (`docs/contracts.md`).

`ConversationDetail.active_judge_session` is always `None` in M1: no
`judge_session` table exists yet (`data-model.md` -> Conversation and judge
state; M1 PRD -> Out of scope). Once a later milestone introduces Judge
Mode, the field's type becomes `JudgeSessionState | None`, matching
`docs/contracts.md` verbatim.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from .enums import MessageRole, Route


class ConversationSummary(BaseModel):
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: UUID
    seq: int
    turn_id: UUID
    role: MessageRole
    content: str
    payload: dict[str, Any] | None = None
    route: Route | None = None
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageOut]  # all messages, ascending seq
    active_judge_session: None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]  # degraded if database is down
    database: Literal["ok", "unavailable"]
    langfuse: Literal["ok", "disabled", "unavailable"]
    rules_version: str | None
    card_source_version: str | None
