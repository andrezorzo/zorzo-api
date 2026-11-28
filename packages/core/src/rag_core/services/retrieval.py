"""Retrieval (ADR 0001, ADR 0009): hybrid search over one corpus version, then the rerank chain."""

from collections.abc import MutableMapping, Sequence

import structlog

from rag_core.limits import Limits
from rag_core.ports import Embedder, Reranker, SearchHit, TraceSink, VectorStore
from rag_core.services.stream import stage

log = structlog.get_logger()


async def search(
    question: str,
    version: int,
    *,
    embedder: Embedder,
    store: VectorStore,
    limits: Limits,
    kinds: Sequence[str] | None = None,
    vectors: MutableMapping[str, list[float]] | None = None,
) -> list[SearchHit]:
    """`vectors`, when given, holds the texts embedded so far: the question's is read from it or added."""
    vector = vectors.get(question) if vectors is not None else None
    if vector is None:
        [vector] = await embedder.embed([question])
        if vectors is not None:
            vectors[question] = vector
    return await store.search(vector, question, limits.retrieval_top_k, version, kinds)


async def rerank(
    question: str, hits: list[SearchHit], *, rerankers: Sequence[Reranker], limits: Limits
) -> list[SearchHit]:
    """The best `rerank_top_n`; a failing level falls to the next, and last to the fused order."""
    if not hits:
        return []
    for level in rerankers:
        try:
            ranked = await level.rerank(question, hits)
            return ranked[: limits.rerank_top_n]
        except Exception:
            log.warning("rerank_level_failed", level=type(level).__name__, exc_info=True)
    return hits[: limits.rerank_top_n]


async def retrieve(
    question: str,
    corpus_version: int,
    sink: TraceSink,
    embedder: Embedder,
    store: VectorStore,
    rerankers: Sequence[Reranker],
    limits: Limits,
    kinds: Sequence[str] | None = None,
    vectors: MutableMapping[str, list[float]] | None = None,
) -> list[SearchHit]:
    """Search, then rerank, each as a step of the stream."""
    with stage(sink, "retrieve"):
        hits = await search(
            question,
            corpus_version,
            embedder=embedder,
            store=store,
            limits=limits,
            kinds=kinds,
            vectors=vectors,
        )
    with stage(sink, "rerank"):
        return await rerank(question, hits, rerankers=rerankers, limits=limits)
