"""Error taxonomy (`docs/contracts.md` -> Error taxonomy, HTTP error mapping).

`ErrorCode` is scoped to the subset M1's surfaces can actually raise (PRD ->
Interfaces and contracts affected): `ambiguous_match` (cards), `tool_not_allowed`
/ `tool_limit_exceeded` (tool loop), `structured_output_invalid` /
`citation_validation_failed` / `insufficient_evidence` (judge/rules), and
`safeguard_rejected` (model-based screening, M8) are not reachable yet and are
added by the milestone that introduces their surface.

`model_limit_exceeded` *is* reachable in M1: the model gateway raises it when
the next `complete()` call would exceed `Settings.model_calls_max`
(`contracts.md` -> Operational limits), which an operator can trigger with
`MODEL_CALLS_MAX=0`. M1's single `other`-route call never reaches the default
cap of 8 on its own.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ErrorCode(StrEnum):
    validation_error = "validation_error"
    not_found = "not_found"
    conflict = "conflict"
    dependency_unavailable = "dependency_unavailable"
    timeout = "timeout"
    model_limit_exceeded = "model_limit_exceeded"
    internal_error = "internal_error"


class ErrorInfo(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = {}


class ErrorResponse(BaseModel):
    error: ErrorInfo


class ManaLeakError(Exception):
    """Single exception class carrying `ErrorCode` (contracts.md -> Error
    taxonomy). Adapters map it to `ToolError`, `ErrorResponse`/HTTP status,
    SSE `error`, CLI exit code, or MCP tool error."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details if details is not None else {}
