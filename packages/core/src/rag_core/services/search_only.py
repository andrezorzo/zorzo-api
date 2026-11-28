"""Search-only answer (LLD "Stream events", degraded mode): the best passages instead of a written answer.

Questions in a degraded mode take this path from the start; the agent graph ends here too when its models
fail before the draft or the time runs short (LLD "6.3 Agent run").
"""

import asyncio
import functools
from collections.abc import Callable, MutableMapping, Sequence
from uuid import UUID

import structlog

from rag_core.events import Claim, ContactOption, DegradedReason, Done, Usage
from rag_core.limits import Limits
from rag_core.ports import ActiveCorpus, Embedder, LanguageDetector, Reranker, TraceSink, VectorStore
from rag_core.services import retrieval
from rag_core.services.answer_cache import AnswerCacheService, end_with_hit
from rag_core.services.guard import CanaryWatch
from rag_core.services.stream import emit_degraded, end_with_error, stage
from rag_core.services.verification import publishable
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
    vectors: MutableMapping[str, list[float]] | None = None,
) -> None:
    """Emits the whole stream, ending with `done`; a bug becomes `error` then `done`, never an exception.
    `vectors` holds the texts already embedded (the cache lookup's), so the question isn't embedded twice."""
    try:
        # A hung embedder or database must not keep the visitor's stream open (LLD "6.3 Agent run").
        loop_now = asyncio.get_running_loop().time()
        async with asyncio.timeout_at(
            deadline_at if deadline_at is not None else loop_now + limits.request_deadline
        ):
            hits = await retrieval.retrieve(
                question, corpus.version, sink, embedder, store, rerankers, limits, vectors=vectors
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
    link_hosts: Sequence[str] = (),
    canary: CanaryWatch,
    contact: Sequence[ContactOption] = (),
) -> list[Claim] | None:
    """A question asked in a degraded mode (LLD "6.9 Answer cache", in degraded mode): the first question of
    a conversation is looked up in the answer cache with no model call, and a hit is served as a cached
    answer; everything else, a miss included, is search-only. Never raises. Returns the claims of a hit, or
    None."""
    started = asyncio.get_running_loop().time()
    vectors: dict[str, list[float]] = {}
    if first_question:
        publishable_now = functools.partial(publishable, link_hosts=link_hosts, canary=canary)
        served, vector, hit = await _served_from_cache(
            question, corpus, trace_id, cache, detector, sink, deadline_at, contact, started, publishable_now
        )
        if served:
            return hit
        if vector is not None:
            vectors[question] = vector  # the lookup embedded it: retrieval doesn't again
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
        vectors=vectors,
    )
    return None


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
    publishable_now: Callable[[Sequence[Claim]], bool],
) -> tuple[bool, list[float] | None, list[Claim] | None]:
    """Looks the raw question up and, on a hit, sends the whole stream. Returns whether it did, and the
    question's vector when the lookup embedded it (a miss, with nothing sent but the `cache` stage), and the
    claims sent.
    Everything that can fail runs before the first `claim`, so a failure is a miss."""
    try:
        lang = detector.detect(question)
        if lang is None or cache.skips(question, lang):
            return False, None, None
        with stage(sink, "cache"):
            found = await cache.lookup(question, lang, corpus.version, deadline_at, publishable_now)
    except Exception as exc:
        log.warning("cache_lookup_failed", error=type(exc).__name__)
        return False, None, None
    if found.claims is None:
        return False, found.vector, None
    latency = round((asyncio.get_running_loop().time() - started) * MS_PER_S)
    usage = Usage(tokens_in=0, tokens_out=0, cost_usd=0.0, latency_ms=latency)
    end_with_hit(sink, found.claims, usage, trace_id, contact)
    return True, None, found.claims
