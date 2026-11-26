"""Structured logs (LLD "Logs"): JSON in production, readable locally; every line carries `trace_id`."""

import logging

import structlog
import structlog.tracebacks

_TRACEBACKS = structlog.processors.ExceptionRenderer(
    structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
)


def configure_logging(app_env: str, level: str = "INFO") -> None:
    renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer(
            # Plain tracebacks never print frame locals, which can hold document text or a visitor's question.
            exception_formatter=structlog.dev.plain_traceback
        )
        if app_env == "local"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # JSONRenderer does not format exceptions; without this, log.exception keeps no traceback.
            # show_locals=False: frame locals can hold secrets or a visitor's question.
            *([] if app_env == "local" else [_TRACEBACKS]),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
    )
    # Library loggers (uvicorn, sqlalchemy, psycopg) follow the same level.
    logging.getLogger().setLevel(level)
    # Except the HTTP clients': at `info` httpx logs every request URL, and Telegram's URL holds the bot
    # token; LiteLLM logs every call (and, at `debug`, the request).
    for name in ("httpx", "httpcore", "LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
        logging.getLogger(name).setLevel(max(logging.WARNING, logging.getLevelNamesMapping()[level]))
