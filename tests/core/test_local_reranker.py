"""AC4: the local rerank level with the real ONNX cross-encoder (downloaded once, cached)."""

import os
from pathlib import Path

from rag_core.adapters.local_reranker import LocalReranker
from rag_core.ports import SearchHit

CACHE = Path(os.environ.get("RERANK_CACHE_DIR", Path.home() / ".cache" / "zorzo-rerank"))


def test_reranks_with_the_local_cross_encoder() -> None:
    hits = [
        SearchHit(1, "Recipe", "Receita de bolo de cenoura com cobertura de chocolate.", 0.03),
        SearchHit(2, "Career", "André usou AWS Lambda, Kinesis e DynamoDB em sistemas distribuídos.", 0.02),
        SearchHit(3, "Site", "The site answers only from reviewed sources and shows citations.", 0.01),
    ]
    ranked = LocalReranker(CACHE).rerank("Quais serviços da AWS o André usou?", hits)
    assert ranked[0].id == 2
    assert {h.id for h in ranked} == {1, 2, 3}
    assert ranked[0].score > ranked[-1].score
