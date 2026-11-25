"""Maps every failure before the stream opens to the error catalog, and logs one line per request."""

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from rag_core.errors import DEFAULT_MESSAGE, STATUS, ApiError, ErrorCode, error_body
from starlette.exceptions import HTTPException as StarletteHTTPException

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
        started = time.perf_counter()
        # Bind trace_id to this request's context: every log line emitted while it runs, in any layer,
        # carries the same id without passing it around. Contextvars keep concurrent async requests apart.
        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            try:
                response = await call_next(request)
            except Exception:
                log.exception("unhandled_error")
                response = _error_response(ErrorCode.INTERNAL, DEFAULT_MESSAGE[ErrorCode.INTERNAL], trace_id)
            # trace_id is passed explicitly too, so the line carries it under any processor chain.
            log.info(
                "request",
                trace_id=trace_id,
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                ms=round((time.perf_counter() - started) * 1000),
            )
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.code, exc.message, request.state.trace_id, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
        code = ErrorCode.INVALID_INPUT
        return _error_response(code, DEFAULT_MESSAGE[code], request.state.trace_id)

    # Routing failures (no route, wrong method) answer with the catalog body too (LLD "Errors").
    routing = {404: ErrorCode.NOT_FOUND, 405: ErrorCode.METHOD_NOT_ALLOWED}

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        code = routing.get(exc.status_code)
        if code is None:
            return await http_exception_handler(request, exc)  # no route raises others today
        headers = dict(exc.headers) if exc.headers else None  # 405 keeps `Allow`
        return _error_response(code, DEFAULT_MESSAGE[code], request.state.trace_id, headers)
