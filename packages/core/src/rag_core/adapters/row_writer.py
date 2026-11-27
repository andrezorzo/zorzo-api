"""One row written in the background (never awaited by the request), with a failure only logged."""

from typing import Any

import structlog
from sqlalchemy import TextClause

from rag_core.adapters.postgres import Database
from rag_core.background import BackgroundTasks

log = structlog.get_logger()


class RowWriter:
    def __init__(self, db: Database, insert: TextClause, failure_event: str) -> None:
        self.db = db
        self._insert = insert
        self._failure_event = failure_event
        self._background = BackgroundTasks()

    def write(self, params: dict[str, Any], **context: Any) -> None:
        """`context` is what the failure's log line names (never the row's text)."""
        self._background.spawn(self._write(params, context))

    async def _write(self, params: dict[str, Any], context: dict[str, Any]) -> None:
        try:
            async with self.db.short_transaction() as conn:
                await conn.execute(self._insert, params)
        except Exception as exc:
            log.error(self._failure_event, error=type(exc).__name__, **context)

    async def drain(self, timeout: float | None = None) -> None:
        await self._background.drain(timeout)
