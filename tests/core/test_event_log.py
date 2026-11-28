"""The event log's writes are capped: a flood of events can't pile up unbounded background inserts."""

import asyncio
from contextlib import asynccontextmanager
from typing import Any, cast

from rag_core.adapters.event_log import MAX_PENDING_ROWS, PostgresEventLog
from structlog.testing import capture_logs


class _BlockedDatabase:
    """A database whose transactions never end until `release` is set."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.inserts = 0

    @asynccontextmanager
    async def short_transaction(self) -> Any:
        self.inserts += 1
        await self.release.wait()
        yield self

    async def execute(self, *_: Any) -> None:
        return None


async def test_writes_past_the_cap_are_dropped_with_one_warning() -> None:
    db = _BlockedDatabase()
    event_log = PostgresEventLog(cast(Any, db))
    with capture_logs() as logs:
        for _ in range(MAX_PENDING_ROWS + 50):
            event_log.write("warning", "gate", "rate_limited", {"buckets": ["ip_burst"]})
        await asyncio.sleep(0)
    assert db.inserts == MAX_PENDING_ROWS
    dropped = [entry for entry in logs if entry["event"] == "event_log_dropped"]
    assert len(dropped) == 1  # throttled: one line per burst, not one per row
    db.release.set()
    await event_log.drain()
    event_log.write("warning", "gate", "rate_limited", {})  # room again
    await event_log.drain()
    assert db.inserts == MAX_PENDING_ROWS + 1
