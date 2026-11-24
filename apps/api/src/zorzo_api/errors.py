"""Maps every failure before the stream opens to the error catalog, and logs one line per request."""

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from zorzo_core.errors import DEFAULT_MESSAGE, STATUS, ApiError, ErrorCode, error_body

log = structlog.get_logger()


def _error_response(
    code: ErrorCode, message: str, trace_id: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(error_body(code, message, trace_id), status_code=STATUS[code], headers=headers)


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def trace_and_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = str(uuid.uuid4())
        request.state.trace_id = trace_id
        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_error")
            response = _error_response(ErrorCode.INTERNAL, DEFAULT_MESSAGE[ErrorCode.INTERNAL], trace_id)
        log.info(
            "request",
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round((time.perf_counter() - started) * 1000),
        )
        structlog.contextvars.unbind_contextvars("trace_id")
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.code, exc.message, request.state.trace_id, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
        code = ErrorCode.INVALID_INPUT
        return _error_response(code, DEFAULT_MESSAGE[code], request.state.trace_id)
