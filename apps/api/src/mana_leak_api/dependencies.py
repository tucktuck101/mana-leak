"""FastAPI dependency providers for the shared core (`docs/contracts.md` ->
Core service interfaces, Turn orchestration).

WP5 is cut in the same wave as WP4, which builds `mana_leak_core.orchestrator`
and `mana_leak_core.conversations` (plan -> Notes). Every provider below
imports its real function lazily (inside the function body) so this module,
and the app, can be imported before those modules exist. WP5's own tests
override every provider with a fake via FastAPI dependency overrides (plan
-> Notes), so the lazy import never actually runs in this worktree; the
orchestrator re-runs WP5's suite against the real modules once WP4 merges.
"""

from collections.abc import AsyncIterator, Awaitable, Callable

from mana_leak_core.contracts.conversations import ConversationDetail, ConversationSummary
from mana_leak_core.contracts.events import TurnEvent

CreateConversation = Callable[..., Awaitable[ConversationSummary]]
ListConversations = Callable[..., Awaitable[list[ConversationSummary]]]
GetConversation = Callable[..., Awaitable[ConversationDetail]]
ProcessTurn = Callable[..., AsyncIterator[TurnEvent]]


def get_create_conversation() -> CreateConversation:
    from mana_leak_core.conversations import create_conversation

    return create_conversation


def get_list_conversations() -> ListConversations:
    from mana_leak_core.conversations import list_conversations

    return list_conversations


def get_get_conversation() -> GetConversation:
    from mana_leak_core.conversations import get_conversation

    return get_conversation


def get_process_turn() -> ProcessTurn:
    from mana_leak_core.orchestrator import process_turn

    return process_turn
