"""What the graph's steps pass to each other (LLD "6.3 Agent run")."""

from typing import Annotated, TypedDict

from langgraph.channels.untracked_value import UntrackedValue

from rag_core.events import BlockCategory, DegradedReason, GuardLayer, Outcome
from rag_core.ports import SearchHit
from rag_core.services.route import RouteResult
from rag_core.services.verification import AnswerContract, CheckedClaim


class Block(TypedDict):
    """Why a run was blocked: the layer and category of the `blocked` event."""

    layer: GuardLayer
    category: BlockCategory


class AgentState(TypedDict, total=False):
    question: str  # the normalized question
    route: RouteResult
    query: str  # what retrieval searches: the standalone question, or the reformulated query
    passages: list[SearchHit]  # in rerank order, trimmed to the prompt's budget; cited by number
    skip_assess: bool  # the best passage scored past `ASSESS_SKIP_SCORE`
    reformulate: bool  # `assess` found the evidence weak and a rewrite is still allowed
    reformulated: int
    answer: AnswerContract
    claims: list[CheckedClaim]
    repairs: int
    outcome: Outcome  # set when the run ends
    degraded: DegradedReason  # set when the run goes search-only
    # Per run only (UntrackedValue): never checkpointed, so a blocked question can't reach the cache or the
    # history (cross-task decision X3).
    blocked: Annotated[Block, UntrackedValue]
