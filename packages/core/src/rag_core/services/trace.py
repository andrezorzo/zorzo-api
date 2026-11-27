"""Trace sinks and the stored trace (LLD "Components and dependencies", TraceSink and TraceStore).

One step event goes to every sink: the visitor's stream, the recorder that becomes the stored trace, and
LangSmith.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from rag_core.events import Done, Model, Stage, StreamEvent, Token, Usage
from rag_core.ports import SpanRow, StoredTrace, TraceFacts, TraceSink
from rag_core.services.redact import Redactor
from rag_core.units import MS_PER_S

log = structlog.get_logger()


class FanOutSink:
    """The first sink is the visitor's stream; a failing extra sink is logged and skipped, never fatal."""

    def __init__(self, sinks: list[TraceSink]) -> None:
        if not sinks:
            raise ValueError("FanOutSink needs the stream sink first")
        self._primary, *self._extra = sinks

    def record(self, event: StreamEvent) -> None:
        self._primary.record(event)
        for sink in self._extra:
            try:
                sink.record(event)
            except Exception:
                log.warning("trace_sink_failed", sink=type(sink).__name__, kind=event.kind, exc_info=True)

    def finish(self, facts: TraceFacts) -> None:
        """Tell every sink that has a `finish` the request is over; one failing doesn't stop the rest."""
        for sink in (self._primary, *self._extra):
            finish: Callable[[TraceFacts], None] | None = getattr(sink, "finish", None)
            if finish is None:
                continue
            try:
                finish(facts)
            except Exception:
                log.warning("trace_sink_failed", sink=type(sink).__name__, kind="finish", exc_info=True)


@dataclass
class _OpenStep:
    started_at: datetime
    models: list[Model]


class TraceRecorder:
    """A TraceSink that keeps what the stored trace needs, each step with the moment it started (`record` is
    synchronous, so that is when it was sent). `build` turns it into the `StoredTrace`."""

    def __init__(
        self, started_at: datetime, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._started_at = started_at
        self._clock = clock
        self._open: dict[str, _OpenStep] = {}
        self._spans: list[SpanRow] = []
        self._cut: list[str] = []  # steps that started and never ended
        self._events: list[StreamEvent] = []
        self._cost = 0.0
        self._done: Done | None = None
        self._done_at = started_at

    def record(self, event: StreamEvent) -> None:
        if isinstance(event, Token | Usage):  # the draft and the totals aren't stored
            return
        if isinstance(event, Stage):
            self._stage(event)
        elif isinstance(event, Model):
            self._cost += event.cost_usd
            if (step := self._open.get(event.stage)) is not None:
                step.models.append(event)
        elif isinstance(event, Done):
            self._done, self._done_at = event, self._clock()
        else:
            self._events.append(event)

    def _stage(self, event: Stage) -> None:
        if event.status == "start":
            if event.name in self._open:  # started again with no end in between
                self._cut.append(event.name)
            self._open[event.name] = _OpenStep(self._clock(), [])
        elif (step := self._open.pop(event.name, None)) is not None:
            self._spans.append(_span(event.name, step, event.ms or 0))

    def build(self, facts: TraceFacts, redactor: Redactor) -> StoredTrace | None:
        """The trace of a request that reached `done`, its events redacted (`facts.question` already is);
        None without `done` (no outcome is known)."""
        if self._done is None:
            return None
        events: dict[str, Any] = {
            "events": [{"kind": e.kind, **e.model_dump(mode="json")} for e in self._events],
            "cut": [*self._cut, *self._open],
            # `trace_id` and `outcome` have columns of their own.
            "done": self._done.model_dump(mode="json", exclude={"trace_id", "outcome"}),
        }
        return StoredTrace(
            facts=facts,
            outcome=self._done.stored_outcome,
            latency_ms=round((self._done_at - self._started_at).total_seconds() * MS_PER_S),
            cost_usd=self._cost,
            expires_at=self._clock() + facts.retention,
            payload={"question": facts.question, **redactor.walk(events)},
            spans=self._spans,
        )


def _span(step: str, opened: _OpenStep, ms: int) -> SpanRow:
    """A step's span: its model calls summed, the last one naming the model and its fallback reason."""
    if not opened.models:
        return SpanRow(step, opened.started_at, ms, None, None, None, None, None, 0.0)
    last = opened.models[-1]
    return SpanRow(
        step=step,
        started_at=opened.started_at,
        latency_ms=ms,
        model=last.model,
        provider=last.provider,
        fallback_reason=last.fallback_reason,
        tokens_in=sum(m.tokens_in for m in opened.models),
        tokens_out=sum(m.tokens_out for m in opened.models),
        cost_usd=sum(m.cost_usd for m in opened.models),
    )
