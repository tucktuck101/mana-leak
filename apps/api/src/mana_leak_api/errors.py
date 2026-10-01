"""HTTP error mapping (`docs/contracts.md` -> HTTP error mapping, Error taxonomy).

`ManaLeakError` is the single exception every core function raises
(`docs/contracts.md` -> Error taxonomy); this module is the one place that
maps its `ErrorCode` to an HTTP status, and replaces FastAPI's default 422
body-validation handler so both failure paths produce the same
`ErrorResponse` shape (contracts.md:795 -- "FastAPI default handler is
replaced to emit `ErrorResponse` with `validation_error`").

Only the `ErrorCode` members M1's surfaces can raise are mapped
(`mana_leak_core.contracts.errors.ErrorCode`); a later milestone extends
this table alongside the enum it maps.
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
        _STATUS_BY_CODE[exc.code],
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
