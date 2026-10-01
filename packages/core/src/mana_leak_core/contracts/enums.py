"""Core enums (`docs/contracts.md` -> Core enums, Audit events).

Only the enums M1's surfaces need are defined here; the rest of the full
`docs/contracts.md` enum list (`RulingStatus`, `ScreeningLabel`, `ComboSource`,
...) arrives with the milestone that introduces its domain.
"""

from enum import StrEnum


class Route(StrEnum):
    """`docs/contracts.md` -> Core enums. M1 always resolves to `other`
    (PRD FR-5); the other members exist because `Route` is a shared type
    used by `TurnEvent.Final.route` and `MessageOut.route` from day one."""

    cards = "cards"
    combos = "combos"
    judge = "judge"
    other = "other"


class MessageRole(StrEnum):
    user = "user"
    assistant = "assistant"
    tool = "tool"


class Severity(StrEnum):
    info = "info"
    warning = "warning"
    error = "error"


class AuditEventType(StrEnum):
    """`docs/contracts.md` -> Audit events. M1 only ever emits `timeout` and
    `limit_reached` (PRD FR-7, NFR-4); the remaining members are defined now
    because `emit_audit_event`'s signature (WP2) is fixed against the full
    enum and later milestones must not redefine it."""

    screening_result = "screening_result"
    route_selected = "route_selected"
    tool_rejected = "tool_rejected"
    tool_failed = "tool_failed"
    timeout = "timeout"
    retry_exhausted = "retry_exhausted"
    limit_reached = "limit_reached"
    judge_transition = "judge_transition"
    citation_validation_failed = "citation_validation_failed"
    structured_output_invalid = "structured_output_invalid"
    dependency_degraded = "dependency_degraded"
