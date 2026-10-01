"""Error taxonomy (`docs/contracts.md` -> Error taxonomy, HTTP error mapping).

`ErrorCode` is scoped to the subset M1's surfaces can actually raise (PRD ->
Interfaces and contracts affected): `ambiguous_match` (cards), `tool_not_allowed`
/ `tool_limit_exceeded` (tool loop), `model_limit_exceeded` (M1's `other` route
never nears the call budget from a single short answer),
`structured_output_invalid` / `citation_validation_failed` / `insufficient_evidence`
(judge/rules), and `safeguard_rejected` (model-based screening, M8) are not
reachable yet and are added by the milestone that introduces their surface.
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
