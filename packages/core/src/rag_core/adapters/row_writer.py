"""One row written in the background (never awaited by the request), with a failure only logged. The writer
of the `log_events` and the `defense_events` rows: a flood of either (429s, refusals) is dropped past a cap,
so it can't pile up tasks and memory behind a slow database."""

from typing import Any

import structlog
from sqlalchemy import TextClause

from rag_core.adapters.postgres import Database
from rag_core.background import BackgroundTasks

log = structlog.get_logger()

# Rows waiting to be inserted; past it a flood is dropped. Each pending row is a task holding a few hundred
# bytes, so 1000 stay under a megabyte, and a database that keeps up never gets near it.
MAX_PENDING_ROWS = 1000
# A burst of drops ends only when the pending rows fall below this, so a flood that frees one slot and
# fills it again stays one burst (one warning).
REARM_PENDING_ROWS = MAX_PENDING_ROWS // 2


class RowWriter:
    """`name` prefixes the log events: `<name>_failed`, `<name>_full` (a burst starts) and `<name>_dropped`
    (the burst's count)."""

    def __init__(
        self, db: Database, insert: TextClause, name: str, *, max_pending: int = MAX_PENDING_ROWS
    ) -> None:
        self.db = db
        self._insert = insert
        self._name = name
        self._max_pending = max_pending
        self._rearm_pending = max_pending // 2
        self._background = BackgroundTasks()
        self._dropped = 0  # rows dropped in the burst going on; a burst logs one warning and one count

    @property
    def pending(self) -> int:
        """Rows waiting to be inserted."""
        return self._background.pending

    def write(self, params: dict[str, Any], **context: Any) -> None:
        """`context` is what the failure's log line names (never the row's text)."""
        if self.pending >= self._max_pending:
            if self._dropped == 0:
                log.warning(f"{self._name}_full", max_pending=self._max_pending)
            self._dropped += 1
            return
        self._report_dropped()
        task = self._background.spawn(self._write(params, context))
        task.add_done_callback(lambda _: self._report_dropped())  # a flood that stops still gets its count

    def _report_dropped(self, *, force: bool = False) -> None:
        """Log how many rows the burst dropped, once, when the pending rows are below the re-arm level (or
        `force`, at the end of a drain: nothing more will be written)."""
        if self._dropped and (force or self.pending < self._rearm_pending):
            log.warning(f"{self._name}_dropped", dropped=self._dropped, pending=self.pending)
            self._dropped = 0

    async def _write(self, params: dict[str, Any], context: dict[str, Any]) -> None:
        try:
            async with self.db.short_transaction() as conn:
                await conn.execute(self._insert, params)
        except Exception as exc:
            log.error(f"{self._name}_failed", error=type(exc).__name__, **context)

    async def drain(self, timeout: float | None = None) -> None:
        await self._background.drain(timeout)
        # Rows dropped after this forced report are logged only when the pending rows later fall below
        # the re-arm level (an insert ending, or a new write): accepted at shutdown, where nothing more
        # is written.
        self._report_dropped(force=True)
