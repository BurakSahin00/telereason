"""The one error shape {"error": {"code", "message"}} for every response (docs/api.md)."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from telereason.ai.engine import EngineNotReadyError
from telereason.api.schemas import ErrorCode, ErrorDetail, ErrorResponse

# Status codes raised by FastAPI or Starlette themselves (404 unknown path, 405 wrong method).
_CODE_BY_STATUS: dict[int, ErrorCode] = {
    404: "not_found",
    409: "busy",
    429: "rate_limited",
    501: "not_implemented",
    503: "engine_not_ready",
}

# Documents the error shape for every route in /openapi.json. A "default" entry also stops
# FastAPI from adding its own 422 HTTPValidationError schema.
ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    "default": {"model": ErrorResponse, "description": "Error"},
}

ENGINE_NOT_READY_MESSAGE = "The model engine is still loading; try again in a few minutes."
INTERNAL_ERROR_MESSAGE = "The server failed while answering; try again."


class ApiError(Exception):
    def __init__(self, status_code: int, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def not_implemented(endpoint: str) -> ApiError:
    return ApiError(501, "not_implemented", f"{endpoint} is not implemented yet.")


def error_response(
    status_code: int,
    code: ErrorCode,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(body.model_dump(), status_code=status_code, headers=headers)


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.status_code, exc.code, exc.message)


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _CODE_BY_STATUS.get(exc.status_code, "bad_request")
    return error_response(exc.status_code, code, str(exc.detail), exc.headers)


async def _engine_not_ready(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, EngineNotReadyError)
    return error_response(503, "engine_not_ready", ENGINE_NOT_READY_MESSAGE)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    parts = []
    for err in exc.errors():
        where = ".".join(str(part) for part in err.get("loc", ()))
        parts.append(f"{where}: {err.get('msg', 'invalid value')}")
    return error_response(422, "bad_request", "; ".join(parts) or "Invalid request.")


async def _unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    # Starlette re-raises the exception after sending this response, so uvicorn logs it.
    return error_response(500, "internal_error", INTERNAL_ERROR_MESSAGE)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(EngineNotReadyError, _engine_not_ready)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unexpected_error)
