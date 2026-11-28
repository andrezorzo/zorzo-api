"""The event log's writes are capped: a flood of events can't pile up unbounded background inserts."""

import asyncio
from contextlib import asynccontextmanager
from typing import Any, cast

from rag_core.adapters.event_log import MAX_PENDING_ROWS, REARM_PENDING_ROWS, PostgresEventLog
from structlog.testing import capture_logs

HOVER_ROUNDS = 20  # a flood that frees one slot, and fills it again, this many times


class _BlockedDatabase:
    """A database whose transactions end only when the test lets them."""

    def __init__(self) -> None:
        self.gates: list[asyncio.Event] = []
        self.finished = 0

    @asynccontextmanager
    async def short_transaction(self) -> Any:
        gate = asyncio.Event()
        self.gates.append(gate)
        await gate.wait()
        self.finished += 1
        yield self

    async def execute(self, *_: Any) -> None:
        return None

    async def let_one_end(self) -> None:
        self.gates[self.finished].set()
        for _ in range(5):  # the task ends within a few turns of the loop
            await asyncio.sleep(0)


def _write(event_log: PostgresEventLog) -> None:
    event_log.write("warning", "gate", "rate_limited", {"buckets": ["ip_burst"]})


async def test_writes_past_the_cap_are_dropped() -> None:
    db = _BlockedDatabase()
    event_log = PostgresEventLog(cast(Any, db))
    for _ in range(MAX_PENDING_ROWS + 50):
        _write(event_log)
    await asyncio.sleep(0)
    assert len(db.gates) == MAX_PENDING_ROWS
    for gate in db.gates:
        gate.set()
    await event_log.drain(timeout=5)


async def test_a_flood_hovering_at_the_cap_logs_once_and_the_count_when_it_ends() -> None:
    db = _BlockedDatabase()
    event_log = PostgresEventLog(cast(Any, db))
    with capture_logs() as logs:
        for _ in range(MAX_PENDING_ROWS):
            _write(event_log)
        await asyncio.sleep(0)  # every insert has begun
        dropped = 0
        for _ in range(HOVER_ROUNDS):
            _write(event_log)  # at the cap: dropped
            _write(event_log)
            dropped += 2
            await db.let_one_end()  # a slot frees up...
            _write(event_log)  # ...and the flood takes it
        assert [e["event"] for e in logs] == ["event_log_full"]  # one warning, not one per freed slot
        while event_log.pending >= REARM_PENDING_ROWS:  # the burst is over
            await db.let_one_end()
        _write(event_log)
    counted = [e for e in logs if e["event"] == "event_log_dropped"]
    assert [e["dropped"] for e in counted] == [dropped]
    assert not any("buckets" in str(e) for e in logs)  # no content
    await asyncio.sleep(0)  # the last insert has begun
    for gate in db.gates:
        gate.set()
    await event_log.drain(timeout=5)
