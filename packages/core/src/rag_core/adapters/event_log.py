"""`log_events` rows for events that belong to no request (LLD "Logs"), written in the background."""

import json
from typing import Any

from sqlalchemy import text

from rag_core.adapters.postgres import Database
from rag_core.adapters.row_writer import RowWriter

INSERT = text("""
INSERT INTO log_events (level, source, component, event, fields)
VALUES (:level, 'app', :component, :event, CAST(:fields AS jsonb))
""")


class PostgresEventLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._writer = RowWriter(db, INSERT, "event_log")

    @property
    def pending(self) -> int:
        """Rows waiting to be inserted."""
        return self._writer.pending

    def write(self, level: str, component: str, event: str, fields: dict[str, Any]) -> None:
        params = {"level": level, "component": component, "event": event, "fields": json.dumps(fields)}
        self._writer.write(params, component=component, event_name=event)

    async def drain(self, timeout: float | None = None) -> None:
        await self._writer.drain(timeout)
