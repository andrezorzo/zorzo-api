"""F01-T10: every reply leaves a stored, redacted record of how it was produced (AC1 to AC5).

The fake provider answers each step as in `test_answer.py`; the database is read directly, since the trace
pages that read it are F02's.
"""

import dataclasses
import json
import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any, cast

import langsmith.utils as langsmith_utils
import psycopg
import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from rag_core.adapters.factory import build_gateway, rerank_chain
from rag_core.events import StreamEvent
from rag_core.models_config import Entry
from rag_core.ports import StoredTrace, StoreUnavailableError
from rag_core.services.redact import Redactor
from structlog.testing import capture_logs

from tests.api.test_answer import ANSWER_A, ANSWER_B, CLAIMS, answer, routed, script, stages, verdicts
from tests.api.test_ask import QUESTION, ask
from tests.conftest import api_settings, build_app, discard_models, reset_tables, seed_budget
from tests.contract.sse import Stream
from tests.fakes.langsmith import FakeLangSmith
from tests.fakes.openrouter import FakeOpenRouter, Reply

PAID = "test/answer-paid"
PAID_COST = 0.000321
ROUTE_A, ROUTE_B = "test/route:free", "test/route-b:free"
AMBIENT_WAIT_S = 1.0  # how long a background post would take to reach the fake endpoint
HUNG_SHUTDOWN_DRAIN_S = 0.5
HUNG_LANGSMITH_BOUND_S = 10  # the stream of a hung LangSmith ends well within the request deadline
MIN_PROMPT_LINE_CHARS = 12  # a shorter line (a heading, a bullet) is too common to prove a leak
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


def test_a_normal_request_logs_no_sink_failure(client: TestClient, fake: FakeOpenRouter) -> None:
    script(fake)
    with capture_logs() as logs:
        ask(client)
    assert [line for line in logs if line["event"] == "trace_sink_failed"] == []


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
    discard_models(app.state.gateway)  # as `build_app` does for the gateway it replaces
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


# -- AC1 and AC3: LangSmith gets the same steps, redacted ----------------------------------------------------

LS_KEY = "lsv2_pt_" + "0123456789abcdef0123456789abcdef_0123456789"
SALT = "test-seed-" * 4  # `api_settings`' salt seed: one of the API's own secret values
OWNER_EMAIL = "andre_zorzo@yahoo.com"  # in the zorzo domain's `pii_allowlist`
EMAIL, PHONE, CPF = "maria.silva@example.com", "+55 11 98765-4321", "529.982.247-25"
HIDDEN_QUESTION = f"I am Maria, {EMAIL}, call me on {PHONE}, my CPF is {CPF}: what AWS services did he use?"
HIDDEN_CLAIMS = [
    (f"Maria ({EMAIL}) asked about AWS.", [1]),
    ("He used Lambda with a zero​width joiner in the text.", [1]),
    (f"The owner's address is {OWNER_EMAIL} and the seed is {SALT}.", [1, 2]),
]


def traced_client(url: str, fake: FakeOpenRouter, langsmith: FakeLangSmith) -> TestClient:
    settings = api_settings(url, langsmith_api_key=LS_KEY, langsmith_endpoint=langsmith.serve())
    return TestClient(build_app(settings, openrouter=fake))


def flush(client: TestClient) -> None:
    app: Any = client.app
    app.state.tracer.flush(10)


def test_langsmith_gets_the_same_steps(corpus_database: str, fake: FakeOpenRouter) -> None:
    langsmith = FakeLangSmith()
    script(fake)
    with traced_client(corpus_database, fake, langsmith) as client:
        stream = ask(client)
        flush(client)
    root_id = stream.only("done")[0].data["trace_id"]
    runs = langsmith.runs()
    [root] = [r for r in runs if r["id"] == root_id]
    assert root["name"] == "ask" and root.get("parent_run_id") is None
    children = sorted((r for r in runs if r.get("parent_run_id") == root_id), key=lambda r: r["start_time"])
    assert [c["name"] for c in children if c["run_type"] == "chain"] == stages(stream)
    assert root["outputs"]["outcome"] == "answered"
    assert root["outputs"]["claims"] == [e.data["text"] for e in stream.only("claim")]
    models = [r for r in runs if r["run_type"] == "llm"]
    assert [m["extra"]["metadata"]["ls_model_name"] for m in models] == [
        e.data["model"] for e in stream.only("model")
    ]
    assert all(m["outputs"]["usage_metadata"]["input_tokens"] == 100 for m in models)


def test_langsmith_is_off_without_a_key(
    corpus_database: str, fake: FakeOpenRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    langsmith = FakeLangSmith()
    # LangChain's own tracing, switched on by the environment, would post the graph's runs to the endpoint.
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", langsmith.serve())
    cast(Any, langsmith_utils.get_env_var).cache_clear()  # the SDK reads these once
    script(fake)
    try:
        with TestClient(build_app(api_settings(corpus_database), openrouter=fake)) as client:
            assert ask(client).names()[-1] == "done"
        time.sleep(AMBIENT_WAIT_S)  # a background post would arrive by now
    finally:
        monkeypatch.undo()
        cast(Any, langsmith_utils.get_env_var).cache_clear()
    assert langsmith.received == []


def test_a_hung_langsmith_never_holds_the_stream(
    corpus_database: str, fake: FakeOpenRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rag_api.main.SHUTDOWN_DRAIN_S", HUNG_SHUTDOWN_DRAIN_S)  # the hang isn't waited out
    langsmith = FakeLangSmith()
    langsmith.mode = "hang"
    script(fake)
    with traced_client(corpus_database, fake, langsmith) as client:
        started = time.monotonic()
        stream = ask(client)
        assert time.monotonic() - started < HUNG_LANGSMITH_BOUND_S
        assert stream.names()[-1] == "done"
        assert "claim" in stream.names()
        trace = trace_of(corpus_database, stream)
        assert trace["outcome"] == "answered"


def all_stored_text(url: str) -> str:
    with psycopg.connect(url) as conn:
        rows = [
            conn.execute(f"SELECT coalesce(string_agg(to_jsonb(t)::text, ' '), '') FROM {table} t").fetchone()
            for table in ("public_traces", "trace_spans", "questions_log")
        ]
    return " ".join(row[0] for row in rows if row)


def test_the_stored_trace_and_langsmith_are_redacted(corpus_database: str, fake: FakeOpenRouter) -> None:
    reset_tables(corpus_database, "questions_log", "public_traces")
    langsmith = FakeLangSmith()
    script(
        fake,
        draft=answer(HIDDEN_CLAIMS),
        judge=verdicts((0, "supported"), (1, "supported"), (2, "supported")),
    )
    with traced_client(corpus_database, fake, langsmith) as client:
        app: Any = client.app
        # The local database's password ("zorzo") is a substring of the owner's allowlisted address, so the
        # API's own secrets would cut it; this test checks the allowlist, with the secrets it quotes.
        app.state.redactor = Redactor(app.state.domain.pii_allowlist, [SALT, LS_KEY])
        stream = ask(client, {**QUESTION, "q": HIDDEN_QUESTION})
        flush(client)
        prompts = [line.strip() for p in app.state.prompts.model_dump().values() for line in p.splitlines()]
    assert stream.only("done")[0].data["outcome"] == "answered"
    assert any(OWNER_EMAIL in e.data["text"] for e in stream.only("claim"))  # the visitor's own stream: as is
    for where, text in (("stored", all_stored_text(corpus_database)), ("langsmith", langsmith.text())):
        for leaked in (EMAIL, "98765", CPF, SALT, "​", "\\u200b"):
            assert leaked not in text, (where, leaked)
        for line in (p for p in prompts if len(p) >= MIN_PROMPT_LINE_CHARS):
            assert line not in text and json.dumps(line)[1:-1] not in text, (where, line)
        for placeholder in ("[email]", "[phone]", "[cpf]", "[secret]"):
            assert placeholder in text, (where, placeholder)
        assert OWNER_EMAIL in text  # the domain's allowlist passes
