"""Structured logs (LLD "Logs"): JSON in production, readable locally; every line carries `trace_id`."""

import importlib.util
import logging

import structlog
import structlog.tracebacks

_TRACEBACKS = structlog.processors.ExceptionRenderer(
    structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
)


def configure_logging(app_env: str, level: str = "INFO") -> None:
    renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer(
            # Frame locals can hold document text or a visitor's question, even in local runs.
            exception_formatter=(
                structlog.dev.RichTracebackFormatter(show_locals=False)
                if importlib.util.find_spec("rich")
                else structlog.dev.plain_traceback
            )
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
