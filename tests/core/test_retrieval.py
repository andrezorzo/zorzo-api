"""AC4: the retrieval service orders hits by the reranker and falls back to the fused order."""

from pathlib import Path

from rag_core.limits import load_limits
from rag_core.ports import SearchHit
from rag_core.services.retrieval import rerank, search

LIMITS = load_limits(Path(__file__).resolve().parents[2] / "config/limits.yaml").model_copy(
    update={"rerank_top_n": 3}
)
FUSED = [SearchHit(i, f"t{i}", f"c{i}", 1.0 / i) for i in range(1, 6)]


class Embedder:
    tag = "t"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]


class Store:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    async def active_corpus(self) -> None:
        return None

    async def search(self, vector: list[float], text: str, k: int, version: int) -> list[SearchHit]:
        self.calls.append((text, k, version))
        return FUSED


class Reverse:
    def warm(self) -> None:
        pass

    async def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        return [SearchHit(h.id, h.title, h.content, float(n)) for n, h in enumerate(reversed(hits))]


class Broken:
    def warm(self) -> None:
        pass

    async def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        raise RuntimeError("model missing")


async def test_orders_hits_by_the_reranker() -> None:
    store = Store()
    fused = await search("q", 7, embedder=Embedder(), store=store, limits=LIMITS)
    hits = await rerank("q", fused, rerankers=[Reverse()], limits=LIMITS)
    assert [h.id for h in hits] == [5, 4, 3]
    assert store.calls == [("q", 20, 7)]


async def test_falls_back_to_fused_order_when_the_reranker_fails() -> None:
    fused = await search("q", 7, embedder=Embedder(), store=Store(), limits=LIMITS)
    hits = await rerank("q", fused, rerankers=[Broken()], limits=LIMITS)
    assert [h.id for h in hits] == [1, 2, 3]
