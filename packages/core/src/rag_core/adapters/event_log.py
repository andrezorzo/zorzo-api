"""`log_events` rows for events that belong to no request (LLD "Logs"), written in the background."""

import json
from typing import Any

import structlog
from sqlalchemy import text

from rag_core.adapters.postgres import Database
from rag_core.background import BackgroundTasks

log = structlog.get_logger()

INSERT = text("""
INSERT INTO log_events (level, source, component, event, fields)
VALUES (:level, 'app', :component, :event, CAST(:fields AS jsonb))
""")


class PostgresEventLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._background = BackgroundTasks()

    def write(self, level: str, component: str, event: str, fields: dict[str, Any]) -> None:
        self._background.spawn(self._insert(level, component, event, fields))

    async def _insert(self, level: str, component: str, event: str, fields: dict[str, Any]) -> None:
        params = {"level": level, "component": component, "event": event, "fields": json.dumps(fields)}
        try:
            async with self.db.short_transaction() as conn:
                await conn.execute(INSERT, params)
        except Exception as exc:
            log.error("event_log_failed", component=component, event_name=event, error=type(exc).__name__)

    async def drain(self, timeout: float | None = None) -> None:
        await self._background.drain(timeout)
