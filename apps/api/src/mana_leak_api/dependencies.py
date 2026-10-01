"""FastAPI dependency providers for the shared core (`docs/contracts.md` ->
Core service interfaces, Turn orchestration).

Each provider returns the real core function. They exist so tests can swap a
capability through `app.dependency_overrides` without patching core modules;
the routers never import the core directly.
"""

from collections.abc import AsyncIterator, Awaitable, Callable

from mana_leak_core.contracts.conversations import ConversationDetail, ConversationSummary
from mana_leak_core.contracts.events import TurnEvent
from mana_leak_core.conversations import create_conversation, get_conversation, list_conversations
from mana_leak_core.orchestrator import process_turn

CreateConversation = Callable[..., Awaitable[ConversationSummary]]
ListConversations = Callable[..., Awaitable[list[ConversationSummary]]]
GetConversation = Callable[..., Awaitable[ConversationDetail]]
ProcessTurn = Callable[..., AsyncIterator[TurnEvent]]


def get_create_conversation() -> CreateConversation:
    return create_conversation


def get_list_conversations() -> ListConversations:
    return list_conversations


def get_get_conversation() -> GetConversation:
    return get_conversation


def get_process_turn() -> ProcessTurn:
    return process_turn
