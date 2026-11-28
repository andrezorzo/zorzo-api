"""F01-T10 AC1: the LangSmith run tree, from events with the moments they arrived."""

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from rag_core.adapters.langsmith_tracer import (
    CLIENT_CLOSE_FLOOR_S,
    CLOSE_MIN_S,
    INFLIGHT_WAIT_MIN_S,
    LangSmithTracer,
    RunSink,
)
from rag_core.events import Stage
from rag_core.ports import TraceFacts
from rag_core.services.redact import Redactor
from structlog.testing import capture_logs

from tests.fakes.langsmith import FakeLangSmith

TIMEOUT_S = 10
KEY = "lsv2_pt_" + "0123456789abcdef0123456789abcdef_0123456789"


def test_steps_a_failure_left_open_are_ended_and_flagged() -> None:
    langsmith = FakeLangSmith()
    tracer = LangSmithTracer(KEY, None, langsmith.serve())
    trace_id = uuid4()
    sink = tracer.sink(trace_id, datetime.now(UTC), Redactor())
    for status in ("start", "start"):  # a step started again with no end in between
        sink.record(Stage(name="route", status=status, ms=None))
    sink.record(Stage(name="retrieve", status="start", ms=None))  # and one that never ends
    sink.finish(TraceFacts(trace_id, "q", None, None, None, 1, "v", timedelta(days=1)))
    tracer.flush(10)
    tracer.close(1)
    runs = langsmith.runs()
    [root] = [r for r in runs if r["id"] == str(trace_id)]
    steps = [r for r in runs if r.get("parent_run_id") == str(trace_id)]
    assert [s["name"] for s in steps] == ["route", "route", "retrieve"]
    assert all(s["end_time"] and "cut" in s["error"] for s in steps)  # every one ended, flagged as cut
    assert root["outputs"]["outcome"] == "error" and root["end_time"]


def test_without_a_key_a_sink_posts_nothing() -> None:
    langsmith = FakeLangSmith()
    tracer = LangSmithTracer(None, None, langsmith.serve())
    sink = tracer.sink(uuid4(), datetime.now(UTC), Redactor())
    sink.record(Stage(name="route", status="start", ms=None))
    sink.finish(TraceFacts(uuid4(), "q", None, None, None, 1, "v", timedelta(days=1)))
    tracer.flush(1)
    assert langsmith.received == []


class _CloseSpy:
    """The SDK client, noting when it is closed (its own `close` attribute is read-only)."""

    def __init__(self, client: Any, order: list[str]) -> None:
        self._client, self._order = client, order

    def close(self, timeout: float) -> None:
        self.timeout = timeout
        self.closed_at = time.monotonic()
        self._order.append("closed")
        self._client.close(timeout=timeout)


def test_a_cancelled_request_is_not_an_error_in_langsmith() -> None:
    langsmith = FakeLangSmith()
    tracer = LangSmithTracer(KEY, None, langsmith.serve())
    trace_id = uuid4()
    sink = tracer.sink(trace_id, datetime.now(UTC), Redactor())
    sink.record(Stage(name="route", status="start", ms=None))
    facts = TraceFacts(trace_id, "q", None, None, None, 1, "v", timedelta(days=1), cancelled=True)
    sink.finish(facts)
    tracer.flush(10)
    tracer.close(1)
    [root] = [r for r in langsmith.runs() if r["id"] == str(trace_id)]
    assert root["outputs"]["outcome"] == "cancelled"


def test_a_run_finished_after_close_is_not_built_or_posted(monkeypatch: pytest.MonkeyPatch) -> None:
    langsmith = FakeLangSmith()
    tracer = LangSmithTracer(KEY, None, langsmith.serve())
    sink = tracer.sink(uuid4(), datetime.now(UTC), Redactor())
    sink.record(Stage(name="route", status="start", ms=None))
    tracer.close(1)  # shutdown, while the request's write was still stuck
    built: list[str] = []
    monkeypatch.setattr(RunSink, "_post", lambda *_: built.append("post"))
    with capture_logs() as logs:
        sink.finish(TraceFacts(uuid4(), "q", None, None, None, 1, "v", timedelta(days=1)))
    assert built == []  # no run tree was begun
    assert langsmith.received == []
    assert [e["event"] for e in logs] == ["langsmith_skipped_closing"]


def test_close_waits_for_a_run_tree_being_posted(monkeypatch: pytest.MonkeyPatch) -> None:
    langsmith = FakeLangSmith()
    tracer = LangSmithTracer(KEY, None, langsmith.serve())
    trace_id = uuid4()
    sink = tracer.sink(trace_id, datetime.now(UTC), Redactor())
    sink.record(Stage(name="route", status="start", ms=None))
    order: list[str] = []
    in_post, release = threading.Event(), threading.Event()
    real_post = RunSink._post

    def blocked_post(self: RunSink, client: Any, facts: TraceFacts) -> None:
        in_post.set()
        assert release.wait(TIMEOUT_S)  # the tree is half-way: the request's thread is stuck here
        real_post(self, client, facts)
        order.append("posted")

    monkeypatch.setattr(RunSink, "_post", blocked_post)
    tracer._client = _CloseSpy(tracer._client, order)  # type: ignore[assignment]
    facts = TraceFacts(trace_id, "q", None, None, None, 1, "v", timedelta(days=1))
    finishing = threading.Thread(target=sink.finish, args=(facts,))
    finishing.start()
    assert in_post.wait(TIMEOUT_S)
    closing = threading.Thread(target=tracer.close, args=(TIMEOUT_S,))
    closing.start()
    assert tracer._shutdown.closing.wait(TIMEOUT_S)  # close has begun and must now be waiting
    assert order == []
    release.set()
    finishing.join(TIMEOUT_S)
    closing.join(TIMEOUT_S)
    assert order == ["posted", "closed"]
    [root] = [r for r in langsmith.runs() if r["id"] == str(trace_id)]
    assert root["end_time"]  # the root's patch was sent too


def test_a_stuck_post_gets_its_wait_and_the_client_its_floor_within_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    langsmith = FakeLangSmith()
    tracer = LangSmithTracer(KEY, None, langsmith.serve())
    sink = tracer.sink(uuid4(), datetime.now(UTC), Redactor())
    sink.record(Stage(name="route", status="start", ms=None))
    in_post, release = threading.Event(), threading.Event()

    def stuck_post(self: RunSink, client: Any, facts: TraceFacts) -> None:
        in_post.set()
        release.wait(TIMEOUT_S)

    monkeypatch.setattr(RunSink, "_post", stuck_post)
    spy = _CloseSpy(tracer._client, [])
    tracer._client = spy  # type: ignore[assignment]
    facts = TraceFacts(uuid4(), "q", None, None, None, 1, "v", timedelta(days=1))
    waits: list[float] = []
    wait_for_posts = tracer._shutdown.close

    def recording_wait(timeout: float) -> None:
        waits.append(timeout)
        wait_for_posts(timeout)

    monkeypatch.setattr(tracer._shutdown, "close", recording_wait)
    finishing = threading.Thread(target=sink.finish, args=(facts,))
    finishing.start()
    assert in_post.wait(TIMEOUT_S)
    started = time.monotonic()
    with capture_logs() as logs:
        tracer.close(CLOSE_MIN_S)  # the post never ends within it
    release.set()
    finishing.join(TIMEOUT_S)
    assert spy.closed_at - started >= INFLIGHT_WAIT_MIN_S  # the post got its wait
    # The split, without the wall clock: the client's floor comes out of the timeout, not on top of it, and
    # the wait used all of its share, so the client gets exactly its floor.
    assert waits == [CLOSE_MIN_S - CLIENT_CLOSE_FLOOR_S]
    assert spy.timeout == CLIENT_CLOSE_FLOOR_S
    [timed_out] = [e for e in logs if e["event"] == "langsmith_close_timed_out"]
    assert timed_out["active"] == 1 and timed_out["waited_s"] >= INFLIGHT_WAIT_MIN_S


def test_with_no_post_in_flight_the_client_gets_the_rest_of_the_timeout() -> None:
    langsmith = FakeLangSmith()
    tracer = LangSmithTracer(KEY, None, langsmith.serve())
    spy = _CloseSpy(tracer._client, [])
    tracer._client = spy  # type: ignore[assignment]
    tracer.close(TIMEOUT_S)
    assert CLIENT_CLOSE_FLOOR_S < spy.timeout <= TIMEOUT_S
