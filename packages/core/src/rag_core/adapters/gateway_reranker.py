"""Remote rerank level (ADR 0009): the gateway's `rerank` stage, in front of the local cross-encoder.

Any failure (an exhausted chain, a quota past the warning) raises, and the retrieval service falls to the
local level.
"""

import asyncio

from rag_core.limits import Limits
from rag_core.ports import SearchHit
from rag_core.services.gateway import ModelGateway


class GatewayReranker:
    def __init__(self, gateway: ModelGateway, limits: Limits) -> None:
        self.gateway = gateway
        self.limits = limits

    def warm(self) -> None:
        pass

    async def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        # The request's own deadline bounds the search-only run around this call.
        deadline_at = asyncio.get_running_loop().time() + self.limits.timeout_rerank
        scores = await self.gateway.rerank(query, [h.content for h in hits], deadline_at=deadline_at)
        scored = [SearchHit(h.id, h.title, h.content, s) for h, s in zip(hits, scores, strict=True)]
        return sorted(scored, key=lambda h: h.score, reverse=True)
