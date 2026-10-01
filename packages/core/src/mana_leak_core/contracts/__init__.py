"""Pydantic contracts for Mana Leak (`docs/contracts.md`).

Adapters import names from `mana_leak_core.contracts` (contracts.md ->
Purpose); submodules exist only to let independent work packages edit
without colliding on one file. As later milestones add card, combo, rules,
and judge contracts, they extend this package rather than redefining
anything that already lives here.
"""

from .conversations import (
    ConversationDetail,
    ConversationSummary,
    HealthResponse,
    MessageOut,
)
from .enums import AuditEventType, MessageRole, Route, Severity
from .errors import ErrorCode, ErrorInfo, ErrorResponse, ManaLeakError
from .events import (
    ErrorEvent,
    EventBase,
    Final,
    MessageEnd,
    MessageStart,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnEvent,
    TurnResult,
)

__all__ = [
    "AuditEventType",
    "ConversationDetail",
    "ConversationSummary",
    "ErrorCode",
    "ErrorEvent",
    "ErrorInfo",
    "ErrorResponse",
    "EventBase",
    "Final",
    "HealthResponse",
    "ManaLeakError",
    "MessageEnd",
    "MessageOut",
    "MessageRole",
    "MessageStart",
    "Route",
    "Severity",
    "TextDelta",
    "ToolEnd",
    "ToolStart",
    "TurnEvent",
    "TurnResult",
]
