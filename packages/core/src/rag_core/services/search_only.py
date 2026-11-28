"""Search-only answer (LLD "Stream events", degraded mode): the best passages instead of a written answer.

Questions in a degraded mode take this path from the start; the agent graph ends here too when its models
fail before the draft or the time runs short (LLD "6.3 Agent run").
"""

import asyncio
from collections.abc import Sequence
from uuid import UUID

import structlog

from rag_core.events import ContactOption, DegradedReason, Done
from rag_core.limits import Limits
from rag_core.ports import ActiveCorpus, Embedder, Reranker, TraceSink, VectorStore
from rag_core.services import retrieval
from rag_core.services.stream import emit_degraded, end_with_error

log = structlog.get_logger()


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
    contact: Sequence[ContactOption] = (),
) -> None:
    """Emits the whole stream, ending with `done`; a bug becomes `error` then `done`, never an exception."""
    try:
        # A hung embedder or database must not keep the visitor's stream open (LLD "6.3 Agent run").
        loop_now = asyncio.get_running_loop().time()
        async with asyncio.timeout_at(
            deadline_at if deadline_at is not None else loop_now + limits.request_deadline
        ):
            hits = await retrieval.retrieve(
                question, corpus.version, sink, embedder, store, rerankers, limits
            )
        # Everything that can fail runs before `degraded`, which may be followed only by its hits and `done`.
        emit_degraded(sink, hits, corpus.version, reason, limits.degraded_max_passages)
    except Exception:
        log.exception("search_only_failed")
        end_with_error(sink, trace_id, contact)
        return
    sink.record(Done(trace_id=trace_id, outcome="degraded", cached=False, contact=list(contact)))
