"""What the graph's steps use (LangGraph `Runtime` context): the services, the request's limits, and what
the request has spent so far."""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from rag_core.domain import Intent, Prompts
from rag_core.events import DegradedReason, Model, Outcome, StageName
from rag_core.limits import Limits
from rag_core.models_config import StageName as ModelStage
from rag_core.ports import Embedder, Reranker, SearchHit, TraceSink, VectorStore
from rag_core.services.gateway import Completion, ModelGateway, ModelsUnavailableError, RequestSpend
from rag_core.services.route import RouteResult, route_schema


@dataclass
class AgentContext:
    gateway: ModelGateway
    embedder: Embedder
    store: VectorStore
    rerankers: Sequence[Reranker]
    limits: Limits
    prompts: Prompts
    intents: Mapping[str, Intent]  # the domain's intents: what each one retrieves and does
    languages: Sequence[str]  # the domain's answer languages; the first is the default
    sink: TraceSink
    corpus_version: int
    deadline_at: float  # loop time
    spend: RequestSpend
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    # Read by the runner when the deadline cuts a step, outside the graph's state: whether a `token` reached
    # the visitor, and the passages last found (untrimmed), for a search-only end.
    drafted: bool = False
    found: list[SearchHit] = field(default_factory=list)
    settled: Outcome | None = None  # set once a step sent the stream's ending (claims, or search-only)

    @property
    def route_schema(self) -> type[RouteResult]:
        """The route output narrowed to the domain's intents, so a routed intent is always one of them."""
        return route_schema(tuple(self.intents))

    def seconds_left(self) -> float:
        return self.deadline_at - asyncio.get_running_loop().time()

    @property
    def before_answer(self) -> float:
        """The deadline of the steps before `answer`, which keeps `ANSWER_RESERVE` (LLD "6.3 Agent run")."""
        return self.deadline_at - self.limits.answer_reserve

    def unavailable(self) -> DegradedReason:
        """Why a step before `answer` went search-only: out of time for the answer, or the models failed."""
        return "deadline" if self.seconds_left() < self.limits.answer_reserve else "models_unavailable"

    def record(self, step: StageName, completion: Completion) -> None:
        """Add a model call's usage and cost, and tell the visitor which model answered."""
        self.tokens_in += completion.usage.tokens_in
        self.tokens_out += completion.usage.tokens_out
        self.cost_usd += completion.cost_usd
        self.sink.record(
            Model(
                stage=step,
                model=completion.model,
                provider=completion.provider,
                tokens_in=completion.usage.tokens_in,
                tokens_out=completion.usage.tokens_out,
                cost_usd=completion.cost_usd,
                fallback_reason=completion.fallback_reason,
            )
        )

    async def call(
        self,
        step: StageName,
        stage: ModelStage,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        deadline_at: float | None = None,
    ) -> BaseModel | None:
        """One schema-bound model call under the request's spend and the given deadline (the request's by
        default); None when the chain can't answer."""
        try:
            completion = await self.gateway.complete(
                stage,
                messages,
                schema=schema,
                deadline_at=deadline_at if deadline_at is not None else self.deadline_at,
                spend=self.spend,
            )
        except ModelsUnavailableError:
            return None
        self.record(step, completion)
        return completion.parsed
