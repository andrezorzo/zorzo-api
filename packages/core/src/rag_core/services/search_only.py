"""Search-only answer (LLD "Stream events", degraded mode): the best passages instead of a written answer.

Questions in a degraded mode take this path from the start; the agent graph ends here too when its models
fail before the draft or the time runs short (LLD "6.3 Agent run").
"""

import asyncio
from collections.abc import Sequence
from uuid import UUID

import structlog

from rag_core.events import ContactOption, DegradedReason, Done, Usage
from rag_core.limits import Limits
from rag_core.ports import ActiveCorpus, Embedder, LanguageDetector, Reranker, TraceSink, VectorStore
from rag_core.services import retrieval
from rag_core.services.answer_cache import AnswerCacheService
from rag_core.services.stream import emit_degraded, end_with_error, stage
from rag_core.units import MS_PER_S

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


async def run_degraded(
    question: str,
    corpus: ActiveCorpus,
    trace_id: UUID,
    *,
    cache: AnswerCacheService,
    detector: LanguageDetector,
    first_question: bool,
    sink: TraceSink,
    embedder: Embedder,
    store: VectorStore,
    rerankers: Sequence[Reranker],
    limits: Limits,
    reason: DegradedReason,
    deadline_at: float,
    contact: Sequence[ContactOption] = (),
) -> None:
    """A question asked in a degraded mode (LLD "6.9 Answer cache", in degraded mode): the first question of
    a conversation is looked up in the answer cache with no model call, and a hit is served as a cached
    answer; everything else, a miss included, is search-only. Never raises."""
    started = asyncio.get_running_loop().time()
    if first_question and await _served_from_cache(
        question, corpus, trace_id, cache, detector, sink, deadline_at, contact, started
    ):
        return
    await run_search_only(
        question,
        corpus,
        trace_id,
        sink=sink,
        embedder=embedder,
        store=store,
        rerankers=rerankers,
        limits=limits,
        reason=reason,
        deadline_at=deadline_at,
        contact=contact,
    )


async def _served_from_cache(
    question: str,
    corpus: ActiveCorpus,
    trace_id: UUID,
    cache: AnswerCacheService,
    detector: LanguageDetector,
    sink: TraceSink,
    deadline_at: float,
    contact: Sequence[ContactOption],
    started: float,
) -> bool:
    """Looks the raw question up and, on a hit, sends the whole stream; False on a miss, with nothing sent
    but the `cache` stage. Everything that can fail runs before the first `claim`, so a failure is a miss."""
    try:
        lang = detector.detect(question)
        if lang is None or cache.skips(question, lang):
            return False
        with stage(sink, "cache"):
            found = await cache.lookup(question, lang, corpus.version, deadline_at)
    except Exception as exc:
        log.warning("cache_lookup_failed", error=type(exc).__name__)
        return False
    if found.claims is None:
        return False
    cache.replay(sink, found.claims)
    latency = round((asyncio.get_running_loop().time() - started) * MS_PER_S)
    sink.record(Usage(tokens_in=0, tokens_out=0, cost_usd=0.0, latency_ms=latency))
    sink.record(
        Done(trace_id=trace_id, outcome="answered", cached=True, suggestion=None, contact=list(contact))
    )
    return True
