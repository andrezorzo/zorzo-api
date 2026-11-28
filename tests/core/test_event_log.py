"""The event log's writes are capped: a flood of events can't pile up unbounded background inserts."""

import asyncio
from contextlib import asynccontextmanager
from typing import Any, cast

from rag_core.adapters.event_log import PostgresEventLog
from rag_core.adapters.row_writer import MAX_PENDING_ROWS, REARM_PENDING_ROWS
from structlog.testing import capture_logs

SETTLE_TURNS = 5  # turns of the event loop for a task to end
DRAIN_TIMEOUT_S = 5
STUCK_DRAIN_S = 0.1  # a drain that gives up on inserts that never end
HOVER_ROUNDS = 20  # a flood that frees one slot, and fills it again, this many times


class _BlockedDatabase:
    """A database whose transactions end only when the test lets them."""

    def __init__(self) -> None:
        self.gates: list[asyncio.Event] = []
        self.finished = 0
        self.ignores_cancel = False

    @asynccontextmanager
    async def short_transaction(self) -> Any:
        gate = asyncio.Event()
        self.gates.append(gate)
        try:
            await gate.wait()
        except asyncio.CancelledError:
            if not self.ignores_cancel:
                raise
            await gate.wait()  # stuck for good: a drain's cancel doesn't end it
        self.finished += 1
        yield self

    async def execute(self, *_: Any) -> None:
        return None

    async def let_one_end(self) -> None:
        self.gates[self.finished].set()
        for _ in range(SETTLE_TURNS):  # the task ends within a few turns of the loop
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
    await event_log.drain(timeout=DRAIN_TIMEOUT_S)


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
    await event_log.drain(timeout=DRAIN_TIMEOUT_S)


async def test_the_count_is_logged_when_the_flood_stops_with_no_further_write() -> None:
    db = _BlockedDatabase()
    event_log = PostgresEventLog(cast(Any, db))
    for _ in range(MAX_PENDING_ROWS + 7):
        _write(event_log)
    await asyncio.sleep(0)
    with capture_logs() as logs:
        while (
            event_log.pending >= REARM_PENDING_ROWS
        ):  # the last insert to end brings it below; no write follows
            await db.let_one_end()
    assert [e["dropped"] for e in logs if e["event"] == "event_log_dropped"] == [7]
    await asyncio.sleep(0)
    for gate in db.gates:
        gate.set()
    with capture_logs() as later:
        await event_log.drain(timeout=DRAIN_TIMEOUT_S)
    assert not [e for e in later if e["event"] == "event_log_dropped"]  # once, and the count was reset


async def test_drain_logs_the_count_even_when_inserts_are_stuck() -> None:
    db = _BlockedDatabase()
    db.ignores_cancel = True
    event_log = PostgresEventLog(cast(Any, db))
    for _ in range(MAX_PENDING_ROWS + 3):
        _write(event_log)
    await asyncio.sleep(0)
    with capture_logs() as logs:
        await event_log.drain(timeout=STUCK_DRAIN_S)  # nothing ends: pending stays at the cap
    assert [e["dropped"] for e in logs if e["event"] == "event_log_dropped"] == [3]
    assert event_log.pending >= REARM_PENDING_ROWS  # no done callback can have reported it
    for gate in db.gates:
        gate.set()
    await event_log.drain(timeout=DRAIN_TIMEOUT_S)
