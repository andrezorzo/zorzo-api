"""`defense_events` rows (LLD "Defense events"): written in the background, never holding the question."""

import uuid

import structlog
from sqlalchemy import text

from rag_core.adapters.postgres import Database
from rag_core.background import BackgroundTasks

log = structlog.get_logger()

INSERT = text("""
INSERT INTO defense_events (trace_id, layer, category, action)
VALUES (:trace_id, :layer, :category, :action)
""")


class PostgresDefenseLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._background = BackgroundTasks()

    def record(self, layer: str, category: str, action: str, trace_id: uuid.UUID) -> None:
        self._background.spawn(self._insert(layer, category, action, trace_id))

    async def _insert(self, layer: str, category: str, action: str, trace_id: uuid.UUID) -> None:
        params = {"trace_id": trace_id, "layer": layer, "category": category, "action": action}
        try:
            async with self.db.short_transaction() as conn:
                await conn.execute(INSERT, params)
        except Exception as exc:
            log.error("defense_log_failed", layer=layer, category=category, error=type(exc).__name__)

    async def drain(self, timeout: float | None = None) -> None:
        await self._background.drain(timeout)
