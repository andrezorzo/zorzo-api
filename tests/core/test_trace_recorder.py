"""The recorder keeps what the stored trace needs; the steps a request left open are named once."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from rag_core.events import Done, Stage
from rag_core.ports import TraceFacts
from rag_core.services.redact import Redactor
from rag_core.services.trace import TraceRecorder


def test_a_step_cut_twice_is_named_once() -> None:
    recorder = TraceRecorder(datetime.now(UTC))
    for _ in range(2):  # started again with no end in between, and never ended
        recorder.record(Stage(name="route", status="start", ms=None))
    recorder.record(Stage(name="retrieve", status="start", ms=None))
    recorder.record(Done(trace_id=uuid4(), outcome="abstained", cached=False, suggestion=None))
    facts = TraceFacts(uuid4(), "q", None, None, None, 1, "v", timedelta(days=1))
    trace = recorder.build(facts, Redactor())
    assert trace is not None
    assert trace.payload["cut"] == ["route", "retrieve"]
