"""`log_events` rows for events that belong to no request (LLD "Logs"), written in the background."""

import asyncio
import json
from typing import Any

import structlog
from sqlalchemy import text

from rag_core.adapters.postgres import Database

log = structlog.get_logger()

INSERT = text("""
INSERT INTO log_events (level, source, component, event, fields)
VALUES (:level, 'app', :component, :event, CAST(:fields AS jsonb))
""")


class PostgresEventLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._tasks: set[asyncio.Task[None]] = set()

    def write(self, level: str, component: str, event: str, fields: dict[str, Any]) -> None:
        task = asyncio.get_running_loop().create_task(self._insert(level, component, event, fields))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _insert(self, level: str, component: str, event: str, fields: dict[str, Any]) -> None:
        params = {"level": level, "component": component, "event": event, "fields": json.dumps(fields)}
        try:
            async with self.db.engine.begin() as conn:
                await conn.execute(INSERT, params)
        except Exception as exc:
            log.error("event_log_failed", component=component, event_name=event, error=type(exc).__name__)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
