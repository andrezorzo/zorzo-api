"""Search-only answer (LLD "Stream events", degraded mode): the best passages instead of a written answer.

Until the agent graph exists (F01-T06) every question takes this path; afterwards it stays the degraded one.
"""

import time
from collections.abc import Sequence
from uuid import UUID

import structlog

from rag_core.events import Degraded, DegradedReason, Done, Error, Hit, Retrieval, Stage, StageName
from rag_core.limits import Limits
from rag_core.ports import ActiveCorpus, Embedder, Reranker, TraceSink, VectorStore
from rag_core.services import retrieval

log = structlog.get_logger()


class _Timed:
    def __init__(self, sink: TraceSink, name: StageName) -> None:
        self.sink, self.name = sink, name

    def __enter__(self) -> None:
        self.started = time.perf_counter()
        self.sink.record(Stage(name=self.name, status="start", ms=None))

    def __exit__(self, *exc: object) -> None:
        if exc[0] is None:
            ms = round((time.perf_counter() - self.started) * 1000)
            self.sink.record(Stage(name=self.name, status="end", ms=ms))


async def run_search_only(
    question: str,
    corpus: ActiveCorpus,
    trace_id: UUID,
    *,
    sink: TraceSink,
    embedder: Embedder,
    store: VectorStore,
    rerankers: Sequence[Reranker],
    limits: Limits,
    reason: DegradedReason = "models_unavailable",
) -> None:
    """Emits the whole stream, ending with `done`; a bug becomes `error` then `done`, never an exception."""
    try:
        with _Timed(sink, "retrieve"):
            hits = await retrieval.search(
                question, corpus.version, embedder=embedder, store=store, limits=limits
            )
        with _Timed(sink, "rerank"):
            hits = await retrieval.rerank(question, hits, rerankers=rerankers, limits=limits)
        sink.record(Degraded(reason=reason))
        shown = [Hit(id=h.id, title=h.title, score=h.score) for h in hits[: limits.degraded_max_passages]]
        sink.record(Retrieval(hits=shown, corpus_version=corpus.version))
    except Exception:
        log.exception("search_only_failed")
        sink.record(Error(code="internal", message="Something went wrong on our side."))
    sink.record(Done(trace_id=trace_id, cached=False, abstained=False, suggestion=None))
