"""Conversation service (`docs/contracts.md` -> Core service interfaces
(`# conversations`), Conversation behaviour; `docs/data-model.md` ->
Conversation and judge state).

Every function here owns its own session (`get_session()`), so adapters and
the turn orchestrator never pass one around: `docs/contracts.md` -> Core
service interfaces says "`db` is an `AsyncSession` obtained from
`get_session()`" and the documented signatures carry no session parameter.

M1 scope (PRD -> Out of scope):

- `get_active_judge_session` is not implemented: no `judge_session` table
  exists yet, so `ConversationDetail.active_judge_session` is always `None`
  (`contracts/conversations.py`, WP1).
- `build_context` returns the last 10 turns and whatever `conversation.summary`
  holds. Summarisation is M8, so in M1 that column is never written and the
  field is always `None`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mana_leak_core.contracts.conversations import (
    ConversationDetail,
    ConversationSummary,
    MessageOut,
)
from mana_leak_core.contracts.enums import MessageRole, Route
from mana_leak_core.contracts.errors import ErrorCode, ManaLeakError
from mana_leak_core.db.models import Conversation, Message
from mana_leak_core.db.session import get_session
from mana_leak_core.settings import get_settings

TITLE_MAX_CHARS = 80


class ModelContext(BaseModel):
    """What `build_context` hands the model (`docs/contracts.md` -> Core
    service interfaces: "summary + last 10 turns + active Judge facts").

    `contracts.md` names this type but does not give its fields, so it is
    defined here, next to the only function that produces it. Active Judge
    facts are absent in M1 because no `judge_session` table exists yet; the
    milestone that adds Judge Mode adds the field.
    """

    conversation_id: UUID
    summary: str | None = None
    messages: list[MessageOut] = []


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    """One session per service call, from the shared `get_session()` factory."""
    sessions = get_session()
    session = await anext(sessions)
    try:
        yield session
    finally:
        await sessions.aclose()


def _summary_of(conversation: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message_out(message: Message) -> MessageOut:
    return MessageOut(
        id=message.id,
        seq=message.seq,
        turn_id=message.turn_id,
        role=MessageRole(message.role),
        content=message.content,
        payload=message.payload,
        route=Route(message.route) if message.route is not None else None,
        created_at=message.created_at,
    )


def _title_from(content: str) -> str:
    """`conversation.title` is "set from the first user message"
    (`docs/data-model.md` -> `conversation`): its first non-empty line,
    truncated to a short title."""
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if len(first_line) <= TITLE_MAX_CHARS:
        return first_line
    return first_line[: TITLE_MAX_CHARS - 1].rstrip() + "\u2026"


async def _load_for_update(session: AsyncSession, conversation_id: UUID) -> Conversation:
    """Row-lock the conversation so concurrent appends cannot allocate the
    same `message.seq` (unique on (`conversation_id`, `seq`))."""
    result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise ManaLeakError(ErrorCode.not_found, f"conversation {conversation_id} not found")
    return conversation


async def create_conversation(title: str | None = None) -> ConversationSummary:
    """`docs/contracts.md` -> Core service interfaces. A conversation created
    without a title gets one from its first user message (`append_message`)."""
    async with _session() as session:
        conversation = Conversation(title=title)
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
        return _summary_of(conversation)


async def list_conversations(limit: int = 50) -> list[ConversationSummary]:
    """Most recently updated first (`docs/contracts.md` -> REST API:
    `list[ConversationSummary]` "by `updated_at` desc")."""
    if limit < 1:
        raise ManaLeakError(ErrorCode.validation_error, "limit must be at least 1")
    async with _session() as session:
        result = await session.execute(
            select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)
        )
        return [_summary_of(row) for row in result.scalars().all()]


async def get_conversation(conversation_id: UUID) -> ConversationDetail:
    """Full history, ascending `seq` (`docs/contracts.md` -> Conversation
    behaviour). Unknown id -> `not_found` (PRD AC-11)."""
    async with _session() as session:
        result = await session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise ManaLeakError(ErrorCode.not_found, f"conversation {conversation_id} not found")
        messages = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq.asc())
        )
        return ConversationDetail(
            **_summary_of(conversation).model_dump(),
            messages=[_message_out(row) for row in messages.scalars().all()],
        )


async def append_message(
    conversation_id: UUID,
    turn_id: UUID,
    role: MessageRole,
    content: str,
    payload: dict[str, Any] | None = None,
    route: Route | None = None,
    *,
    message_id: UUID | None = None,
) -> MessageOut:
    """Append one message, allocating the next `seq` and bumping
    `conversation.updated_at` (`docs/data-model.md` -> `conversation`,
    `message`).

    `message_id` extends the signature in `docs/contracts.md` with an
    optional, keyword-only argument: `process_turn` must announce the
    assistant message id in `message_start` (`contracts.md` -> Streaming
    events) before the assistant row exists, and persists the row with that
    same id at the end of the turn. Callers that do not care keep the
    documented behaviour (an application-generated id).
    """
    async with _session() as session:
        conversation = await _load_for_update(session, conversation_id)
        next_seq = (
            await session.execute(
                select(func.coalesce(func.max(Message.seq), 0)).where(
                    Message.conversation_id == conversation_id
                )
            )
        ).scalar_one() + 1

        message = Message(
            conversation_id=conversation_id,
            seq=next_seq,
            turn_id=turn_id,
            role=MessageRole(role).value,
            content=content,
            payload=payload,
            route=Route(route).value if route is not None else None,
        )
        if message_id is not None:
            message.id = message_id
        session.add(message)

        if role is MessageRole.user and not conversation.title:
            conversation.title = _title_from(content)
        conversation.updated_at = datetime.now(UTC)

        await session.commit()
        await session.refresh(message)
        return _message_out(message)


async def build_context(conversation_id: UUID) -> ModelContext:
    """Last `Settings.context_turns` (10) turns plus the stored summary.

    A turn is "one user message and everything up to the next user message"
    (`docs/contracts.md` -> Operational limits), so the window starts at the
    10th-from-last user message and includes every later message (assistant
    and tool rows included).
    """
    turns = get_settings().context_turns
    async with _session() as session:
        result = await session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise ManaLeakError(ErrorCode.not_found, f"conversation {conversation_id} not found")

        recent_user_seqs = (
            (
                await session.execute(
                    select(Message.seq)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.role == MessageRole.user.value,
                    )
                    .order_by(Message.seq.desc())
                    .limit(turns)
                )
            )
            .scalars()
            .all()
        )
        first_seq = min(recent_user_seqs) if recent_user_seqs else 0

        messages = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.seq >= first_seq)
            .order_by(Message.seq.asc())
        )
        return ModelContext(
            conversation_id=conversation_id,
            summary=conversation.summary,
            messages=[_message_out(row) for row in messages.scalars().all()],
        )
