"""Structured logs (LLD "Logs"): JSON in production, readable locally; every line carries `trace_id`."""

import logging

import structlog


def configure_logging(app_env: str) -> None:
    renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer() if app_env == "local" else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
