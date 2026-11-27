"""Remote rerank level (ADR 0009): the gateway's `rerank` stage, in front of the local cross-encoder.

Any failure (every free entry failed or cooling down, no time left) raises, and the retrieval service falls
to the local level.
"""

from rag_core.ports import SearchHit
from rag_core.services.gateway import ModelGateway


class GatewayReranker:
    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def warm(self) -> None:
        pass

    async def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        # The port carries no deadline: the call gets the stage's timeout, and the request's deadline, which
        # bounds the whole search-only run around it, cancels it if it comes first.
        scores = await self.gateway.rerank(query, [h.content for h in hits])
        scored = [SearchHit(h.id, h.title, h.content, s) for h, s in zip(hits, scores, strict=True)]
        return sorted(scored, key=lambda h: h.score, reverse=True)
