"""`log_events` rows for events that belong to no request (LLD "Logs"), written in the background."""

import json
from typing import Any

from sqlalchemy import text

from rag_core.adapters.postgres import Database
from rag_core.adapters.row_writer import RowWriter

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
        self._writer = RowWriter(db, INSERT, "event_log_failed")

    def write(self, level: str, component: str, event: str, fields: dict[str, Any]) -> None:
        params = {"level": level, "component": component, "event": event, "fields": json.dumps(fields)}
        self._writer.write(params, component=component, event_name=event)

    async def drain(self, timeout: float | None = None) -> None:
        await self._writer.drain(timeout)
