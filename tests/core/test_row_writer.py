"""F01-T08: the background row writer never piles up without end (a flood of refusals)."""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

import pytest
from rag_core.adapters import row_writer
from rag_core.adapters.row_writer import RowWriter
from sqlalchemy import text
from structlog.testing import capture_logs

INSERT = text("INSERT INTO t VALUES (:n)")


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


async def test_rows_beyond_the_cap_are_dropped_with_one_throttled_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(row_writer, "MAX_PENDING_ROWS", 3)
    db = SlowDb()
    writer = RowWriter(db, INSERT, "test_row_failed")  # type: ignore[arg-type]
    with capture_logs() as logs:
        for n in range(10):
            writer.write({"n": n})
    dropped = [e for e in logs if e["event"] == "rows_dropped"]
    assert len(dropped) == 1 and dropped[0]["dropped"] == 1  # the first drop warns at once; the rest wait
    db.release.set()
    await writer.drain()
    assert db.rows == [0, 1, 2]  # the rows that fit were written
    writer.write({"n": 99})  # room again
    await writer.drain()
    assert db.rows == [0, 1, 2, 99]


async def test_the_warning_comes_again_after_its_interval_with_the_dropped_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(row_writer, "MAX_PENDING_ROWS", 1)
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    db = SlowDb()
    writer = RowWriter(db, INSERT, "test_row_failed")  # type: ignore[arg-type]
    writer.write({"n": 0})
    with capture_logs() as logs:
        for _ in range(4):
            writer.write({"n": 1})  # the first drop warns, three more wait
        clock[0] += row_writer.DROP_WARNING_EVERY_S
        writer.write({"n": 1})
    counts = [e["dropped"] for e in logs if e["event"] == "rows_dropped"]
    assert counts == [1, 4]
    db.release.set()
    await writer.drain()
