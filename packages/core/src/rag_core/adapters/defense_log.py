"""`defense_events` rows (LLD "Defense events"): written in the background, never holding the question."""

import uuid

from sqlalchemy import text

from rag_core.adapters.postgres import Database
from rag_core.adapters.row_writer import RowWriter

INSERT = text("""
INSERT INTO defense_events (trace_id, layer, category, action)
VALUES (:trace_id, :layer, :category, :action)
""")


class PostgresDefenseLog:
    def __init__(self, db: Database) -> None:
        self._writer = RowWriter(db, INSERT, "defense_log")

    def record(self, layer: str, category: str, action: str, trace_id: uuid.UUID) -> None:
        params = {"trace_id": trace_id, "layer": layer, "category": category, "action": action}
        self._writer.write(params, layer=layer, category=category)

    async def drain(self, timeout: float | None = None) -> None:
        await self._writer.drain(timeout)
