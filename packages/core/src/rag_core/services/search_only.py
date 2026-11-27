"""Search-only answer (LLD "Stream events", degraded mode): the best passages instead of a written answer.

Questions in a degraded mode take this path from the start; the agent graph ends here too when its models
fail before the draft or the time runs short (LLD "6.3 Agent run").
"""

import asyncio
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from uuid import UUID

import structlog

from rag_core.errors import DEFAULT_MESSAGE, ErrorCode
from rag_core.events import Degraded, DegradedReason, Done, Error, Hit, Retrieval, Stage, StageName
from rag_core.limits import Limits
from rag_core.ports import ActiveCorpus, Embedder, Reranker, SearchHit, TraceSink, VectorStore
from rag_core.services import retrieval
from rag_core.units import MS_PER_S

log = structlog.get_logger()


@contextmanager
def stage(sink: TraceSink, name: StageName) -> Iterator[None]:
    """`stage` start, then end with its duration; a failure leaves the end out."""
    started = time.perf_counter()
    sink.record(Stage(name=name, status="start", ms=None))
    yield
    sink.record(Stage(name=name, status="end", ms=round((time.perf_counter() - started) * MS_PER_S)))


def end_with_error(sink: TraceSink, trace_id: UUID) -> None:
    """The end of a stream that hit a bug: `error` then `done`, never an open stream."""
    code = ErrorCode.INTERNAL
    sink.record(Error(code=code.value, message=DEFAULT_MESSAGE[code]))
    sink.record(Done(trace_id=trace_id, outcome="error", cached=False, suggestion=None))


def emit_degraded(
    sink: TraceSink, hits: Sequence[SearchHit], corpus_version: int, reason: DegradedReason, limits: Limits
) -> None:
    """`degraded`, then its passages; only `done` may follow."""
    shown = [Hit(id=h.id, title=h.title, score=h.score) for h in hits[: limits.degraded_max_passages]]
    sink.record(Degraded(mode="search_only", reason=reason))
    sink.record(Retrieval(hits=shown, corpus_version=corpus_version))


async def retrieve(
    question: str,
    corpus_version: int,
    sink: TraceSink,
    embedder: Embedder,
    store: VectorStore,
    rerankers: Sequence[Reranker],
    limits: Limits,
    kinds: Sequence[str] | None = None,
) -> list[SearchHit]:
    with stage(sink, "retrieve"):
        hits = await retrieval.search(
            question, corpus_version, embedder=embedder, store=store, limits=limits, kinds=kinds
        )
    with stage(sink, "rerank"):
        return await retrieval.rerank(question, hits, rerankers=rerankers, limits=limits)


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
    deadline_at: float | None = None,
) -> None:
    """Emits the whole stream, ending with `done`; a bug becomes `error` then `done`, never an exception."""
    try:
        # A hung embedder or database must not keep the visitor's stream open (LLD "6.3 Agent run").
        loop_now = asyncio.get_running_loop().time()
        async with asyncio.timeout_at(
            deadline_at if deadline_at is not None else loop_now + limits.request_deadline
        ):
            hits = await retrieve(question, corpus.version, sink, embedder, store, rerankers, limits)
        # Everything that can fail runs before `degraded`, which may be followed only by its hits and `done`.
        emit_degraded(sink, hits, corpus.version, reason, limits)
    except Exception:
        log.exception("search_only_failed")
        end_with_error(sink, trace_id)
        return
    sink.record(Done(trace_id=trace_id, outcome="degraded", cached=False, suggestion=None))
