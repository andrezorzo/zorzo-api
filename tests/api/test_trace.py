"""F01-T10: every reply leaves a stored, redacted record of how it was produced (AC1 to AC5).

The fake provider answers each step as in `test_answer.py`; the database is read directly, since the trace
pages that read it are F02's.
"""

import dataclasses
import json
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import psycopg
import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from rag_core.adapters.factory import build_gateway, rerank_chain
from rag_core.events import StreamEvent
from rag_core.models_config import Entry
from rag_core.ports import StoreUnavailableError
from rag_core.services.trace import StoredTrace
from structlog.testing import capture_logs

from tests.api.test_answer import ANSWER_A, ANSWER_B, CLAIMS, answer, routed, script, stages
from tests.api.test_ask import QUESTION, ask
from tests.conftest import api_settings, build_app, reset_tables, seed_budget
from tests.contract.sse import Stream
from tests.fakes.openrouter import FakeOpenRouter, Reply

PAID = "test/answer-paid"
PAID_COST = 0.000321
ROUTE_A, ROUTE_B = "test/route:free", "test/route-b:free"
TRACE_TABLES = ("questions_log", "public_traces", "log_events", "budget_daily")


@pytest.fixture
def fake() -> FakeOpenRouter:
    return FakeOpenRouter()


@pytest.fixture
def client(corpus_database: str, fake: FakeOpenRouter) -> Iterator[TestClient]:
    reset_tables(corpus_database, *TRACE_TABLES)
    with TestClient(build_app(api_settings(corpus_database), openrouter=fake)) as c:
        yield c
    reset_tables(corpus_database, "budget_daily")


def trace_of(url: str, stream: Stream) -> dict[str, Any]:
    """The `public_traces` row `done` names, with its spans in order."""
    trace_id = stream.only("done")[0].data["trace_id"]
    with psycopg.connect(url, row_factory=dict_row) as conn:
        trace = conn.execute("SELECT * FROM public_traces WHERE id = %s", (trace_id,)).fetchone()
        assert trace is not None, "no public_traces row for done.trace_id"
        spans = conn.execute("SELECT * FROM trace_spans WHERE trace_id = %s ORDER BY id", (trace_id,))
        return {**trace, "spans": spans.fetchall()}


def paid_answer() -> Reply:
    return dataclasses.replace(answer(CLAIMS), cost=PAID_COST)


# -- AC1: the stored trace comes from the stream's events ---------------------------------------------------


def test_the_stored_trace_comes_from_the_streams_events(
    client: TestClient, fake: FakeOpenRouter, corpus_database: str
) -> None:
    script(fake)
    fake.scripts[ANSWER_A].appendleft("error")  # the free answer entries fail, so the paid one answers
    fake.scripts[ANSWER_B].appendleft("error")
    fake.script(PAID, paid_answer())
    stream = ask(client)
    trace = trace_of(corpus_database, stream)

    ends = {(e.data["name"]): e.data["ms"] for e in stream.only("stage") if e.data["status"] == "end"}
    assert [s["step"] for s in trace["spans"]] == stages(stream)  # one span per stage end, in order
    for span in trace["spans"]:
        assert span["latency_ms"] == ends[span["step"]]
    models = {e.data["stage"]: e.data for e in stream.only("model")}
    for span in trace["spans"]:
        if span["step"] in models:
            event = models[span["step"]]
            assert (span["model"], span["provider"]) == (event["model"], event["provider"])
            assert (span["tokens_in"], span["tokens_out"]) == (event["tokens_in"], event["tokens_out"])
            assert span["cost_usd"] == Decimal(str(event["cost_usd"]))
    assert {s["step"] for s in trace["spans"] if s["model"] is None} == {"retrieve", "rerank"}

    spent = sum(e.data["cost_usd"] for e in stream.only("model"))
    assert spent == pytest.approx(PAID_COST) == stream.only("usage")[0].data["cost_usd"]
    assert trace["cost_usd"] == Decimal(str(PAID_COST))
    assert trace["payload"]["events"] == [
        {"kind": e.name, **e.data} for e in stream.events if e.name in ("retrieval", "claim")
    ]
    assert trace["payload"]["question"] == QUESTION["q"]
    assert trace["payload"]["cut"] == []
    assert trace["outcome"] == "answered" and trace["latency_ms"] > 0
    assert trace["corpus_version"] == stream.only("retrieval")[0].data["corpus_version"]
    assert json.dumps(trace["payload"])  # JSON all the way


# -- AC2: written after the stream, in one transaction ------------------------------------------------------


def count_rows(url: str) -> tuple[int, int, int]:
    with psycopg.connect(url) as conn:
        counts = [
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            for table in ("public_traces", "trace_spans", "questions_log")
        ]
    return (counts[0][0], counts[1][0], counts[2][0])  # type: ignore[index]


def test_the_trace_is_written_after_the_stream(
    client: TestClient, fake: FakeOpenRouter, corpus_database: str
) -> None:
    seen: list[tuple[str, tuple[int, int, int]]] = []

    class LooksUp:
        """A sink that asks the database, on every event it receives, whether the trace is there yet."""

        def record(self, event: StreamEvent) -> None:
            seen.append((event.kind, count_rows(corpus_database)))

    app: Any = client.app
    app.state.trace_sinks.append(LooksUp())
    script(fake)
    try:
        stream = ask(client)
    finally:
        app.state.trace_sinks.clear()
    assert [kind for kind, _ in seen] == stream.names()  # every event, `done` last
    assert {rows for _, rows in seen} == {(0, 0, 0)}  # nothing was written while events were sent
    traces, spans, questions = count_rows(corpus_database)
    assert (traces, questions) == (1, 1) and spans == len(stages(stream))
    with psycopg.connect(corpus_database) as conn:
        outcome = conn.execute("SELECT outcome FROM questions_log").fetchone()
    assert outcome == (stream.only("done")[0].data["outcome"],)


def test_a_failed_trace_write_still_ends_the_stream(client: TestClient, fake: FakeOpenRouter) -> None:
    class Down:
        async def write(self, trace: StoredTrace) -> None:
            raise StoreUnavailableError("OperationalError")

    app: Any = client.app
    app.state.trace_store = Down()
    script(fake)
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        stream = ask(client)
    done = stream.only("done")[0].data
    assert stream.names()[-1] == "done"
    [failed] = [line for line in logs if line["event"] == "trace_write_failed"]
    assert failed["log_level"] == "error" and failed["trace_id"] == done["trace_id"]
    assert failed["error"] == "StoreUnavailableError"


# -- AC4: every ending leaves the trace `done` names --------------------------------------------------------


def a_covered_answer(fake: FakeOpenRouter, **_: Any) -> None:
    script(fake)


def an_abstention(fake: FakeOpenRouter, **_: Any) -> None:
    script(fake, draft=answer([], abstained=True))


def a_decline(fake: FakeOpenRouter, **_: Any) -> None:
    script(fake, route=routed("off_topic"))


def a_degraded_mode(url: str, **_: Any) -> None:
    seed_budget(url, 1.0, degraded=True)  # the day's budget is spent: questions go search-only


def failed_models(fake: FakeOpenRouter, **_: Any) -> None:
    fake.always(ROUTE_A, "error")  # `route` can't answer, so the run goes search-only


def a_bug(fake: FakeOpenRouter, monkeypatch: pytest.MonkeyPatch, **_: Any) -> None:
    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr("rag_api.ask.run_agent", broken)


# What `route` decided counts only when it answered: `public_ok` false, and no intent or lang, otherwise.
ROUTED = ("about_andre", "en", True)
UNROUTED = (None, None, False)
ENDINGS = [
    (a_covered_answer, "answered", ROUTED),
    (an_abstention, "abstained", ROUTED),
    (a_decline, "declined", ("off_topic", "en", True)),
    (a_degraded_mode, "degraded", UNROUTED),
    (failed_models, "degraded", UNROUTED),
    (a_bug, "error", UNROUTED),
]


@pytest.mark.parametrize(("ending", "outcome", "routed_facts"), ENDINGS, ids=[e[0].__name__ for e in ENDINGS])
def test_every_ending_leaves_the_trace_done_names(
    client: TestClient,
    fake: FakeOpenRouter,
    corpus_database: str,
    monkeypatch: pytest.MonkeyPatch,
    ending: Callable[..., None],
    outcome: str,
    routed_facts: tuple[str | None, str | None, bool],
) -> None:
    ending(fake=fake, url=corpus_database, monkeypatch=monkeypatch)
    stream = ask(client)
    trace = trace_of(corpus_database, stream)
    assert stream.only("done")[0].data["outcome"] == outcome
    assert trace["outcome"] == outcome
    assert (trace["intent"], trace["lang"], trace["public_ok"]) == routed_facts
    with psycopg.connect(corpus_database) as conn:
        logged = conn.execute("SELECT outcome FROM questions_log").fetchall()
    assert logged == [(outcome,)]


# -- AC5: a fallback is recorded in the model event and the span -------------------------------------------


def with_two_route_entries(app: FastAPI, fake: FakeOpenRouter) -> None:
    """The route chain with a second free entry, which the test config doesn't have."""
    config = app.state.gateway.config
    route = config.stages.route
    second = Entry(provider="openrouter", model=ROUTE_B)
    stages_ = config.stages.model_copy(
        update={"route": route.model_copy(update={"chain": [*route.chain, second]})}
    )
    app.state.gateway = build_gateway(
        config.model_copy(update={"stages": stages_}),
        {"openrouter": "test-key"},
        app.state.db,
        app.state.alerter,
        app.state.limits,
    )
    app.state.rerankers = rerank_chain(app.state.gateway, app.state.local_rerankers)


@pytest.mark.parametrize(
    ("first", "reason"), [("rate_limited", "rate_limited"), ("hang", "timeout"), ("cooling", "cooling_down")]
)
def test_a_fallback_is_recorded_in_the_model_event_and_the_span(
    corpus_database: str, fake: FakeOpenRouter, first: str, reason: str
) -> None:
    reset_tables(corpus_database, *TRACE_TABLES)
    app = build_app(api_settings(corpus_database), limits={"timeout_route": 0.3}, openrouter=fake)
    with_two_route_entries(app, fake)
    with TestClient(app) as client:
        if first == "cooling":  # an earlier request's 429 cooled the first entry
            fake.script_schema("RouteResult", "rate_limited")
            script(fake)
            ask(client)
        else:
            fake.script_schema("RouteResult", first)
        script(fake)
        stream = ask(client)
    trace = trace_of(corpus_database, stream)
    route = next(e.data for e in stream.only("model") if e.data["stage"] == "route")
    assert (route["model"], route["fallback_reason"]) == (ROUTE_B, reason)
    span = next(s for s in trace["spans"] if s["step"] == "route")
    assert (span["model"], span["fallback_reason"]) == (ROUTE_B, reason)
    # Steps on the answer and judge chains answered by their first entry: no reason, in the event or the span.
    for step in ("answer", "verify"):
        event = next(e.data for e in stream.only("model") if e.data["stage"] == step)
        assert event["fallback_reason"] is None
        assert next(s for s in trace["spans"] if s["step"] == step)["fallback_reason"] is None
