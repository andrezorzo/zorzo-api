"""The background row writer is shared by the event log and the defense log: one cap, one re-arm level, one
count of the dropped rows (F01-T08, F01-T10)."""

import asyncio
from contextlib import asynccontextmanager
from typing import Any, cast

from rag_core.adapters.defense_log import PostgresDefenseLog
from rag_core.adapters.row_writer import MAX_PENDING_ROWS, REARM_PENDING_ROWS, RowWriter
from sqlalchemy import text
from structlog.testing import capture_logs

INSERT = text("INSERT INTO t VALUES (:n)")
SMALL_CAP = 4
DRAIN_TIMEOUT_S = 5
TRACE_ID = "11111111-1111-1111-1111-111111111111"


class SlowDb:
    """A database whose transactions wait for `release`, and a record of the rows written."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.rows: list[Any] = []

    @asynccontextmanager
    async def short_transaction(self) -> Any:
        await self.release.wait()
        db = self

        class Conn:
            async def execute(self, statement: Any, params: dict[str, Any]) -> None:
                db.rows.append(params["n"])

        yield Conn()


async def test_rows_beyond_the_cap_are_dropped_and_counted_once_the_burst_ends() -> None:
    db = SlowDb()
    writer = RowWriter(cast(Any, db), INSERT, "test_row", max_pending=SMALL_CAP)
    with capture_logs() as logs:
        for n in range(SMALL_CAP + 6):
            writer.write({"n": n})
    assert [e["event"] for e in logs] == ["test_row_full"]  # one warning for the whole burst
    db.release.set()
    with capture_logs() as after:
        await writer.drain(DRAIN_TIMEOUT_S)
    assert [e["dropped"] for e in after if e["event"] == "test_row_dropped"] == [6]
    assert db.rows == list(range(SMALL_CAP))  # the rows that fit were written
    writer.write({"n": 99})  # room again
    await writer.drain(DRAIN_TIMEOUT_S)
    assert db.rows[-1] == 99


async def test_a_failed_insert_is_logged_with_the_context_and_never_raised() -> None:
    class Broken:
        @asynccontextmanager
        async def short_transaction(self) -> Any:
            raise RuntimeError("secret detail")
            yield

    writer = RowWriter(cast(Any, Broken()), INSERT, "test_row")
    with capture_logs() as logs:
        writer.write({"n": 1}, layer="L1")
        await writer.drain(DRAIN_TIMEOUT_S)
    failed = [e for e in logs if e["event"] == "test_row_failed"]
    assert failed and failed[0]["error"] == "RuntimeError" and failed[0]["layer"] == "L1"
    assert "secret detail" not in str(logs)


async def test_the_defense_log_shares_the_cap_and_the_hysteresis() -> None:
    db = SlowDb()
    defenses = PostgresDefenseLog(cast(Any, db))
    with capture_logs() as logs:
        for _ in range(MAX_PENDING_ROWS + 3):
            defenses.record("L1", "rate_limit", "block", cast(Any, TRACE_ID))
    assert [e["event"] for e in logs] == ["defense_log_full"]
    assert REARM_PENDING_ROWS < MAX_PENDING_ROWS
    db.release.set()
    with capture_logs() as after:
        await defenses.drain(DRAIN_TIMEOUT_S)
    assert [e["dropped"] for e in after if e["event"] == "defense_log_dropped"] == [3]
