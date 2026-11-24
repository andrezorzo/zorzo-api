"""Retrieval (ADR 0001, ADR 0009): hybrid search over one corpus version, then the rerank chain."""

from collections.abc import Sequence

import anyio
import structlog

from rag_core.limits import Limits
from rag_core.ports import Embedder, Reranker, SearchHit, VectorStore

log = structlog.get_logger()


async def search(
    question: str, version: int, *, embedder: Embedder, store: VectorStore, limits: Limits
) -> list[SearchHit]:
    [vector] = await embedder.embed([question])
    return await store.search(vector, question, limits.retrieval_top_k, version)


async def rerank(
    question: str, hits: list[SearchHit], *, rerankers: Sequence[Reranker], limits: Limits
) -> list[SearchHit]:
    """The best `rerank_top_n`; a failing level falls to the next, and last to the fused order."""
    if not hits:
        return []
    for level in rerankers:
        try:
            # The cross-encoder is CPU-bound: keep it off the event loop (LLD "Process model").
            ranked = await anyio.to_thread.run_sync(level.rerank, question, hits)
            return ranked[: limits.rerank_top_n]
        except Exception:
            log.warning("rerank_level_failed", level=type(level).__name__, exc_info=True)
    return hits[: limits.rerank_top_n]
