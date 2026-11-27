"""One row written in the background (never awaited by the request), with a failure only logged."""

import time
from typing import Any

import structlog
from sqlalchemy import TextClause

from rag_core.adapters.postgres import Database
from rag_core.background import BackgroundTasks

log = structlog.get_logger()

# Rows waiting for a connection at once. A flood of refusals (the rows are cheap to cause) must not pile up
# without end behind a slow database: each pending row is a task holding a few hundred bytes, so 1000 stay
# under a megabyte, and a database that keeps up (one short insert, a few ms) never gets near it.
MAX_PENDING_ROWS = 1000
DROP_WARNING_EVERY_S = 60  # one warning per this long about the rows dropped meanwhile


class RowWriter:
    def __init__(self, db: Database, insert: TextClause, failure_event: str) -> None:
        self.db = db
        self._insert = insert
        self._failure_event = failure_event
        self._background = BackgroundTasks()
        self._pending = 0
        self._dropped = 0  # since the last warning
        self._warned_at: float | None = None

    def write(self, params: dict[str, Any], **context: Any) -> None:
        """`context` is what the failure's log line names (never the row's text). Beyond `MAX_PENDING_ROWS`
        waiting rows the row is dropped, and said so in a throttled warning."""
        if self._pending >= MAX_PENDING_ROWS:
            self._drop()
            return
        self._pending += 1
        self._background.spawn(self._write(params, context))

    def _report_dropped(self) -> None:
        """Say how many rows were dropped since the last warning (a burst's tail, once it has drained)."""
        if self._dropped:
            log.warning("rows_dropped", writer=self._failure_event, dropped=self._dropped)
            self._dropped = 0

    def _drop(self) -> None:
        self._dropped += 1
        now = time.monotonic()
        if self._warned_at is None or now - self._warned_at >= DROP_WARNING_EVERY_S:
            log.warning("rows_dropped", writer=self._failure_event, dropped=self._dropped)
            self._warned_at, self._dropped = now, 0

    async def _write(self, params: dict[str, Any], context: dict[str, Any]) -> None:
        try:
            async with self.db.short_transaction() as conn:
                await conn.execute(self._insert, params)
        except Exception as exc:
            log.error(self._failure_event, error=type(exc).__name__, **context)
        finally:
            self._pending -= 1
            if self._pending == 0:  # the burst has drained: its tail is reported in full
                self._report_dropped()

    async def drain(self, timeout: float | None = None) -> None:
        await self._background.drain(timeout)
        self._report_dropped()
