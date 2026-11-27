"""F01-T10 AC1: the LangSmith run tree, from events with the moments they arrived."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from rag_core.adapters.langsmith_tracer import LangSmithTracer
from rag_core.events import Stage
from rag_core.ports import TraceFacts
from rag_core.services.redact import Redactor

from tests.fakes.langsmith import FakeLangSmith

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
