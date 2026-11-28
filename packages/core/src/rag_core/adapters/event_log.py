"""`log_events` rows for events that belong to no request (LLD "Logs"), written in the background."""

import json
from typing import Any

import structlog
from sqlalchemy import text

from rag_core.adapters.postgres import Database
from rag_core.background import BackgroundTasks

log = structlog.get_logger()

# Rows waiting to be inserted; past it a flood (429s, say) is dropped, so it can't pile up tasks and memory.
MAX_PENDING_ROWS = 1000
# A burst of drops ends only when the pending rows fall below this, so a flood that frees one slot and
# fills it again stays one burst (one warning).
REARM_PENDING_ROWS = MAX_PENDING_ROWS // 2

INSERT = text("""
INSERT INTO log_events (level, source, component, event, fields)
VALUES (:level, 'app', :component, :event, CAST(:fields AS jsonb))
""")


class PostgresEventLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._background = BackgroundTasks()
        self._dropped = 0  # rows dropped in the burst going on; a burst logs one warning and one count

    @property
    def pending(self) -> int:
        """Rows waiting to be inserted."""
        return self._background.pending

    def write(self, level: str, component: str, event: str, fields: dict[str, Any]) -> None:
        if self.pending >= MAX_PENDING_ROWS:
            if self._dropped == 0:
                log.warning("event_log_full", max_pending=MAX_PENDING_ROWS)
            self._dropped += 1
            return
        self._report_dropped()
        task = self._background.spawn(self._insert(level, component, event, fields))
        task.add_done_callback(lambda _: self._report_dropped())  # a flood that stops still gets its count

    def _report_dropped(self) -> None:
        """Log how many rows the burst dropped, once, when the pending rows are below the re-arm level."""
        if self._dropped and self.pending < REARM_PENDING_ROWS:
            log.warning("event_log_dropped", dropped=self._dropped)
            self._dropped = 0

    async def _insert(self, level: str, component: str, event: str, fields: dict[str, Any]) -> None:
        params = {"level": level, "component": component, "event": event, "fields": json.dumps(fields)}
        try:
            async with self.db.short_transaction() as conn:
                await conn.execute(INSERT, params)
        except Exception as exc:
            log.error("event_log_failed", component=component, event_name=event, error=type(exc).__name__)

    async def drain(self, timeout: float | None = None) -> None:
        await self._background.drain(timeout)
        self._report_dropped()
