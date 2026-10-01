"""Audit events (`docs/contracts.md` -> Core service interfaces (`# audit`),
Audit events; `docs/data-model.md` -> Audit data -> `audit_event`).

`emit_audit_event` writes one `audit_event` row and never raises: callers
(gateway, orchestrator, API) call it from exception-handling and limit-check
paths where a logging failure must never mask or replace the original
error (WP2 checklist).
"""

import logging
from typing import Any
from uuid import UUID

from mana_leak_core.contracts.enums import AuditEventType, Severity
from mana_leak_core.db.models import AuditEvent
from mana_leak_core.db.session import get_session

logger = logging.getLogger(__name__)


async def emit_audit_event(
    event_type: AuditEventType,
    severity: Severity,
    conversation_id: UUID | None = None,
    turn_id: UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Write one `audit_event` row (`docs/data-model.md` -> `audit_event`).

    Never raises: a failed write is logged and swallowed so a caller that
    audits a failure (e.g. `limit_reached`, `timeout`) never fails itself
    because the write failed.
    """
    event = AuditEvent(
        conversation_id=conversation_id,
        turn_id=turn_id,
        event_type=event_type.value,
        severity=severity.value,
        details=details if details is not None else {},
    )
    try:
        async for session in get_session():
            session.add(event)
            await session.commit()
    except Exception:
        logger.exception(
            "emit_audit_event failed: event_type=%s severity=%s conversation_id=%s turn_id=%s",
            event_type.value,
            severity.value,
            conversation_id,
            turn_id,
        )
