"""Trace sinks and the stored trace (LLD "Components and dependencies", TraceSink and TraceStore).

One step event goes to every sink: the visitor's stream, the recorder that becomes the stored trace, and
LangSmith when a key is set.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog

from rag_core.events import Done, Model, Stage, StreamEvent, Token, Usage
from rag_core.ports import TraceSink
from rag_core.services.redact import Redactor
from rag_core.units import MS_PER_S

log = structlog.get_logger()

# What `payload.events` leaves out: the steps, models, tokens and cost are in the spans, the draft is replaced
# by the claims, and `done` has its own place (LLD "4. Data", "Stored trace").
NOT_AN_EVENT = (Stage, Model, Token, Usage, Done)
DONE_COLUMNS = ("trace_id", "outcome")  # `done`'s fields that have a column of their own


class FanOutSink:
    """The first sink is the visitor's stream; a failing extra sink is logged and skipped, never fatal."""

    def __init__(self, sinks: list[TraceSink]) -> None:
        if not sinks:
            raise ValueError("FanOutSink needs the stream sink first")
        self.sinks = sinks

    def record(self, event: StreamEvent) -> None:
        primary, *extra = self.sinks
        primary.record(event)
        for sink in extra:
            try:
                sink.record(event)
            except Exception:
                log.warning("trace_sink_failed", sink=type(sink).__name__, kind=event.kind, exc_info=True)


@dataclass(frozen=True)
class SpanRow:
    """One step that ended: a `trace_spans` row."""

    step: str
    started_at: datetime
    latency_ms: int
    model: str | None  # the model that answered last; None for a step with no model call
    provider: str | None
    fallback_reason: str | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float


@dataclass(frozen=True)
class TraceFacts:
    """What the request knows that no stream event carries."""

    trace_id: UUID
    question: str  # as the gate normalized it: scrubbed when the trace is made
    intent: str | None  # from `route`, None when it didn't answer
    lang: str | None
    public_ok: bool | None
    corpus_version: int | None
    model_config_version: str | None
    retention: timedelta  # how long the trace lives (`RETENTION_PUBLIC_TRACES`)


@dataclass(frozen=True)
class StoredTrace:
    """What the trace store writes at the end of a request, already redacted."""

    trace_id: UUID
    outcome: str
    intent: str | None
    lang: str | None
    public_ok: bool
    corpus_version: int | None
    model_config_version: str | None
    latency_ms: int
    cost_usd: float
    expires_at: datetime
    question_scrubbed: str
    payload: dict[str, Any]
    spans: list[SpanRow]


@dataclass
class _OpenStep:
    started_at: datetime
    models: list[Model]


class TraceRecorder:
    """A TraceSink that keeps what the stored trace needs, each event with the moment it arrived (`record` is
    synchronous, so that is when it was sent). `finish` turns it into the `StoredTrace`."""

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
        now = self._clock()
        if isinstance(event, Stage):
            self._stage(event, now)
        elif isinstance(event, Model):
            self._cost += event.cost_usd
            if (step := self._open.get(event.stage)) is not None:
                step.models.append(event)
        elif isinstance(event, Done):
            self._done, self._done_at = event, now
        elif not isinstance(event, NOT_AN_EVENT):
            self._events.append(event)

    def _stage(self, event: Stage, now: datetime) -> None:
        if event.status == "start":
            if event.name in self._open:  # started again with no end in between
                self._cut.append(event.name)
            self._open[event.name] = _OpenStep(now, [])
        elif (step := self._open.pop(event.name, None)) is not None:
            self._spans.append(_span(event.name, step, event.ms or 0))

    def finish(self, facts: TraceFacts, redactor: Redactor) -> StoredTrace | None:
        """The trace of a request that reached `done`, redacted; None without it (no outcome is known)."""
        if self._done is None:
            return None
        done = self._done
        question = redactor.text(facts.question)
        payload = redactor.walk(
            {
                "question": question,
                "events": [{"kind": e.kind, **e.model_dump(mode="json")} for e in self._events],
                "cut": [*self._cut, *self._open],
                "done": done.model_dump(mode="json", exclude=set(DONE_COLUMNS)),
            }
        )
        return StoredTrace(
            trace_id=facts.trace_id,
            outcome="cached" if done.cached else done.outcome,
            intent=facts.intent,
            lang=facts.lang,
            public_ok=bool(facts.public_ok),
            corpus_version=facts.corpus_version,
            model_config_version=facts.model_config_version,
            latency_ms=round((self._done_at - self._started_at).total_seconds() * MS_PER_S),
            cost_usd=self._cost,
            expires_at=self._clock() + facts.retention,
            question_scrubbed=question,
            payload=payload,
            spans=self._spans,
        )


def _span(step: str, opened: _OpenStep, ms: int) -> SpanRow:
    """A step's span: its model calls summed, the last one naming the model and its fallback reason."""
    last = opened.models[-1] if opened.models else None
    return SpanRow(
        step=step,
        started_at=opened.started_at,
        latency_ms=ms,
        model=last.model if last else None,
        provider=last.provider if last else None,
        fallback_reason=last.fallback_reason if last else None,
        tokens_in=sum(m.tokens_in for m in opened.models) if last else None,
        tokens_out=sum(m.tokens_out for m in opened.models) if last else None,
        cost_usd=sum(m.cost_usd for m in opened.models),
    )
