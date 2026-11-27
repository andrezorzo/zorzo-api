"""What the graph's steps use (LangGraph `Runtime` context): the services, the request's limits, and what
the request has spent so far."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

from rag_core.domain import Prompts
from rag_core.events import Model, StageName
from rag_core.limits import Limits
from rag_core.ports import Embedder, Reranker, SearchHit, TraceSink, VectorStore
from rag_core.services.gateway import Completion, ModelGateway, RequestSpend


@dataclass
class AgentContext:
    gateway: ModelGateway
    embedder: Embedder
    store: VectorStore
    rerankers: Sequence[Reranker]
    limits: Limits
    prompts: Prompts
    languages: Sequence[str]  # the domain's answer languages; the first is the default
    sink: TraceSink
    corpus_version: int
    deadline_at: float  # loop time
    spend: RequestSpend
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    drafted: bool = False  # a `token` reached the visitor
    passages: list[SearchHit] = field(default_factory=list)  # the latest found, for a search-only end

    def left(self) -> float:
        return self.deadline_at - asyncio.get_running_loop().time()

    def count(self, step: StageName, completion: Completion) -> None:
        """Add a model call's usage and cost, and tell the visitor which model answered."""
        self.tokens_in += completion.usage.tokens_in
        self.tokens_out += completion.usage.tokens_out
        self.cost_usd += completion.cost_usd
        self.sink.record(Model(stage=step, model=completion.model, provider=completion.provider))
