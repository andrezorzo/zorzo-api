"""Stream primitives shared by every answer path (LLD "Stream events"): step events, the passages, the
search-only end and the end of a stream that hit a bug."""

import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from uuid import UUID

from rag_core.errors import DEFAULT_MESSAGE, ErrorCode
from rag_core.events import Degraded, DegradedReason, Done, Error, Hit, Retrieval, Stage, StageName
from rag_core.ports import SearchHit, TraceSink
from rag_core.units import MS_PER_S


@contextmanager
def stage(sink: TraceSink, name: StageName) -> Iterator[None]:
    """`stage` start, then end with its duration; a failure leaves the end out."""
    started = time.perf_counter()
    sink.record(Stage(name=name, status="start", ms=None))
    yield
    sink.record(Stage(name=name, status="end", ms=round((time.perf_counter() - started) * MS_PER_S)))


def emit_retrieval(sink: TraceSink, hits: Sequence[SearchHit], corpus_version: int) -> None:
    sink.record(
        Retrieval(
            hits=[Hit(id=h.id, title=h.title, score=h.score) for h in hits], corpus_version=corpus_version
        )
    )


def emit_degraded(
    sink: TraceSink, hits: Sequence[SearchHit], corpus_version: int, reason: DegradedReason, shown: int
) -> None:
    """`degraded`, then its first `shown` passages; only `done` may follow."""
    sink.record(Degraded(mode="search_only", reason=reason))
    emit_retrieval(sink, hits[:shown], corpus_version)


def end_with_error(sink: TraceSink, trace_id: UUID) -> None:
    """The end of a stream that hit a bug: `error` then `done`, never an open stream."""
    code = ErrorCode.INTERNAL
    sink.record(Error(code=code.value, message=DEFAULT_MESSAGE[code]))
    sink.record(Done(trace_id=trace_id, outcome="error", cached=False, suggestion=None))
