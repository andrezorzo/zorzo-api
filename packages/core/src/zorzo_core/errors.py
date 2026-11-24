"""The HTTP error catalog (LLD "Errors"): one body, stable codes, one status per code.

Errors before the stream opens are plain HTTP responses with
`{"error": {"code", "message", "trace_id"}}`; every route raises `ApiError` instead of building its own.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    BOT_CHECK_FAILED = "bot_check_failed"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    INTERNAL = "internal"


STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.BOT_CHECK_FAILED: 403,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.UNAVAILABLE: 503,
    ErrorCode.INTERNAL: 500,
}

DEFAULT_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.INVALID_INPUT: "The request is missing a field or has an invalid value.",
    ErrorCode.BOT_CHECK_FAILED: "The bot check failed. Reload the page and try again.",
    ErrorCode.RATE_LIMITED: "Too many questions. Try again later.",
    ErrorCode.UNAVAILABLE: "The service is unavailable right now.",
    ErrorCode.INTERNAL: "Something went wrong on our side.",
}


class ApiError(Exception):
    def __init__(self, code: ErrorCode, message: str | None = None, headers: dict[str, str] | None = None):
        self.code = code
        self.message = message or DEFAULT_MESSAGE[code]
        self.headers = headers or {}
        super().__init__(self.message)

    @property
    def status(self) -> int:
        return STATUS[self.code]


def error_body(code: ErrorCode, message: str, trace_id: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code.value, "message": message, "trace_id": trace_id}}
