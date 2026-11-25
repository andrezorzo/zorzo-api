"""Pass-through rerank level for tests and local runs (`RERANKER=fake`, local only): keeps the fused order."""

from rag_core.ports import SearchHit


class FakeReranker:
    def warm(self) -> None:
        pass

    async def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        return list(hits)
