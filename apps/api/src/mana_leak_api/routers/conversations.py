"""`POST /conversations`, `GET /conversations`, `GET /conversations/{id}`
(`docs/contracts.md` -> REST API). Thin adapter: parses/serializes only,
calling the shared core conversation service through the dependency
providers in `dependencies.py` -- it never implements a conversation
decision itself (`AGENTS.md` SS10, Shared core first).

An unknown `conversation_id` makes `get_conversation` raise
`ManaLeakError(not_found)` (`docs/contracts.md` -> Core service interfaces),
mapped to `404` by `errors.py` (AC-11) -- no explicit check needed here.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from mana_leak_core.contracts.conversations import ConversationDetail, ConversationSummary
from pydantic import BaseModel, ConfigDict

from mana_leak_api.dependencies import (
    CreateConversation,
    GetConversation,
    ListConversations,
    get_create_conversation,
    get_get_conversation,
    get_list_conversations,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None


@router.post("", status_code=201, response_model=ConversationSummary)
async def create_conversation_route(
    body: ConversationCreate = ConversationCreate(),  # noqa: B008
    create_conversation: CreateConversation = Depends(get_create_conversation),  # noqa: B008
) -> ConversationSummary:
    return await create_conversation(title=body.title)


@router.get("", response_model=list[ConversationSummary])
async def list_conversations_route(
    limit: int = 50,
    list_conversations: ListConversations = Depends(get_list_conversations),  # noqa: B008
) -> list[ConversationSummary]:
    return await list_conversations(limit=limit)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation_route(
    conversation_id: UUID,
    get_conversation: GetConversation = Depends(get_get_conversation),  # noqa: B008
) -> ConversationDetail:
    return await get_conversation(conversation_id)
