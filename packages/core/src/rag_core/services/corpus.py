"""Which corpus version the API can serve (LLD "6.1 Corpus version")."""

import structlog

from rag_core.errors import ApiError, ErrorCode
from rag_core.ports import ActiveCorpus, VectorStore

log = structlog.get_logger()


async def servable(store: VectorStore, embed_model_tag: str) -> ActiveCorpus:
    """The active version, read on each question so an activation needs no restart.

    No active version, a version embedded by another model, or a database that can't be read all raise
    `unavailable` before the stream opens: mismatched vectors would return nonsense.
    """
    try:
        corpus = await store.active_corpus()
    except Exception as exc:
        log.warning("corpus_check_failed", exc_info=True)
        raise ApiError(ErrorCode.UNAVAILABLE) from exc
    if corpus is None:
        log.warning("corpus_unavailable", reason="no active version")
        raise ApiError(ErrorCode.UNAVAILABLE)
    if corpus.embed_model_tag != embed_model_tag:
        log.warning("corpus_unavailable", reason="embedding model differs", corpus_tag=corpus.embed_model_tag)
        raise ApiError(ErrorCode.UNAVAILABLE)
    return corpus
