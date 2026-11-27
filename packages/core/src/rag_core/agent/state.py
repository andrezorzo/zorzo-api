"""What the graph's steps pass to each other (LLD "6.3 Agent run")."""

from typing import TypedDict

from rag_core.events import DegradedReason, Outcome
from rag_core.ports import SearchHit
from rag_core.services.route import RouteResult
from rag_core.services.verification import AnswerContract, CheckedClaim


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
