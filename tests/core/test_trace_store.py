"""F01-T10 AC2 (integration): the end of a request writes the trace, its spans and its questions-log row in
one transaction, against a real database."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from rag_core.adapters.postgres import Database
from rag_core.adapters.trace_store import PostgresTraceStore
from rag_core.ports import StoreUnavailableError
from rag_core.services.trace import SpanRow, StoredTrace

from tests.conftest import reset_tables

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def a_trace(**changes: Any) -> StoredTrace:
    spans = [
        SpanRow("route", NOW, 120, "test/route:free", "openrouter", "rate_limited", 100, 20, 0.0),
        SpanRow("retrieve", NOW, 35, None, None, None, None, None, 0.0),
    ]
    values: dict[str, Any] = {
        "trace_id": uuid4(),
        "outcome": "answered",
        "intent": "about_andre",
        "lang": "en",
        "public_ok": True,
        "corpus_version": 1,
        "model_config_version": "abc",
        "latency_ms": 900,
        "cost_usd": 0.000321,
        "expires_at": NOW + timedelta(days=30),
        "question_scrubbed": "Which AWS services?",
        "payload": {"question": "Which AWS services?", "events": [], "cut": [], "done": {"cached": False}},
        "spans": spans,
    }
    return StoredTrace(**{**values, **changes})


@pytest.fixture
async def store(database_url: str) -> AsyncIterator[PostgresTraceStore]:
    reset_tables(database_url, "questions_log", "public_traces")
    db = Database(database_url)
    yield PostgresTraceStore(db)
    await db.close()


def rows(url: str) -> tuple[int, int, int]:
    with psycopg.connect(url) as conn:
        found = [
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            for table in ("public_traces", "trace_spans", "questions_log")
        ]
    return tuple(row[0] for row in found if row)  # type: ignore[return-value]


async def test_a_trace_is_written_in_one_transaction(store: PostgresTraceStore, database_url: str) -> None:
    trace = a_trace()
    await store.write(trace)
    assert rows(database_url) == (1, 2, 1)
    with psycopg.connect(database_url) as conn:
        stored = conn.execute(
            "SELECT t.outcome, t.public_ok, t.expires_at, t.payload, l.question_scrubbed, l.outcome "
            "FROM public_traces t JOIN questions_log l ON l.trace_id = t.id WHERE t.id = %s",
            (str(trace.trace_id),),
        ).fetchone()
        steps = conn.execute(
            "SELECT step, fallback_reason FROM trace_spans WHERE trace_id = %s ORDER BY id",
            (str(trace.trace_id),),
        ).fetchall()
    assert stored == ("answered", True, trace.expires_at, trace.payload, "Which AWS services?", "answered")
    assert steps == [("route", "rate_limited"), ("retrieve", None)]


async def test_a_row_the_database_refuses_leaves_nothing(
    store: PostgresTraceStore, database_url: str
) -> None:
    # A NUL can't be stored in a text column: the questions-log row, written last, is refused.
    with pytest.raises(StoreUnavailableError):
        await store.write(a_trace(question_scrubbed="Which\x00 AWS services?"))
    assert rows(database_url) == (0, 0, 0)


async def test_a_refused_trace_leaves_no_question_row(store: PostgresTraceStore, database_url: str) -> None:
    with pytest.raises(StoreUnavailableError):
        await store.write(a_trace(outcome="other"))  # the outcome check
    assert rows(database_url) == (0, 0, 0)
