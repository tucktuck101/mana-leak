"""HTTP error mapping (`docs/contracts.md` -> HTTP error mapping, Error taxonomy).

`ManaLeakError` is the single exception every core function raises
(`docs/contracts.md` -> Error taxonomy); this module is the one place that
maps its `ErrorCode` to an HTTP status, and replaces FastAPI's default 422
body-validation handler so both failure paths produce the same
`ErrorResponse` shape (contracts.md:795 -- "FastAPI default handler is
replaced to emit `ErrorResponse` with `validation_error`").

Only the `ErrorCode` members M1's surfaces can raise are mapped
(`mana_leak_core.contracts.errors.ErrorCode`); a later milestone extends
this table alongside the enum it maps. An unmapped code falls back to `500`
rather than raising `KeyError` here, because a `KeyError` inside the
exception handler would bypass `ErrorResponse` entirely and return FastAPI's
default plain-text 500 body.

`model_limit_exceeded` has no row in contracts.md's HTTP error mapping table:
the model-call cap can only be hit after `message_start`, so it reaches the
client as an in-stream SSE `error` event, never as a status code. It is
mapped to `500` only so the pre-stream path (an adapter calling `complete()`
outside a turn stream) still produces a well-formed `ErrorResponse`.
"""

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mana_leak_core.contracts.errors import ErrorCode, ErrorInfo, ErrorResponse, ManaLeakError

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.validation_error: status.HTTP_400_BAD_REQUEST,
    ErrorCode.not_found: status.HTTP_404_NOT_FOUND,
    ErrorCode.conflict: status.HTTP_409_CONFLICT,
    ErrorCode.dependency_unavailable: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.timeout: status.HTTP_504_GATEWAY_TIMEOUT,
    ErrorCode.model_limit_exceeded: status.HTTP_500_INTERNAL_SERVER_ERROR,
    ErrorCode.internal_error: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _error_response(
    status_code: int,
    code: ErrorCode,
    message: str,
    *,
    retryable: bool = False,
    details: dict | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorInfo(code=code, message=message, retryable=retryable, details=details or {})
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def _handle_mana_leak_error(request: Request, exc: ManaLeakError) -> JSONResponse:
    return _error_response(
        _STATUS_BY_CODE.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
        exc.code,
        exc.message,
        retryable=exc.retryable,
        details=exc.details,
    )


async def _handle_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        ErrorCode.validation_error,
        "request validation failed",
        details={"errors": jsonable_encoder(exc.errors())},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Registers both handlers on `app` (called once from `main.py`)."""
    app.add_exception_handler(ManaLeakError, _handle_mana_leak_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation_error)
