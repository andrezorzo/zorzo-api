"""What the graph's steps pass to each other (LLD "6.3 Agent run").

Only `conversation` is stored by the checkpointer: every other key is an `UntrackedValue`, which lives for one
run (absent at the start of the next) and is never written to Postgres. A key another step adds follows the
same rule unless it is meant to persist.
"""

from typing import Annotated, Any, TypedDict

from langgraph.channels.untracked_value import UntrackedValue

from rag_core.events import Claim, DegradedReason, Notice, Outcome
from rag_core.ports import SearchHit
from rag_core.services.route import RouteResult
from rag_core.services.verification import AnswerContract, CheckedClaim


class AgentState(TypedDict, total=False):
    question: Annotated[str, UntrackedValue]  # the normalized question
    route: Annotated[RouteResult, UntrackedValue]
    # What retrieval searches: the standalone question, or the reformulated query.
    query: Annotated[str, UntrackedValue]
    # In rerank order, trimmed to the prompt's budget; cited by number.
    passages: Annotated[list[SearchHit], UntrackedValue]
    skip_assess: Annotated[bool, UntrackedValue]  # the best passage scored past `ASSESS_SKIP_SCORE`
    # `assess` found the evidence weak and a rewrite is still allowed.
    reformulate: Annotated[bool, UntrackedValue]
    reformulated: Annotated[int, UntrackedValue]
    answer: Annotated[AnswerContract, UntrackedValue]
    claims: Annotated[list[CheckedClaim], UntrackedValue]
    repairs: Annotated[int, UntrackedValue]
    outcome: Annotated[Outcome, UntrackedValue]  # set when the run ends
    degraded: Annotated[DegradedReason, UntrackedValue]  # set when the run goes search-only
    # The standalone question's embedding, kept by a cache miss for the row it may store. The key says the
    # lookup ran and missed.
    query_vector: Annotated[list[float], UntrackedValue]
    cached: Annotated[bool, UntrackedValue]  # the run is a cache hit: `finish` sends its claims
    # What the hit found, already held to the publish checks.
    hit_claims: Annotated[list[Claim], UntrackedValue]
    notice: Annotated[Notice, UntrackedValue]  # an edge question, sent in `done`
    # The session's conversation (`rag_core.services.conversation.Conversation` as plain types): stored.
    conversation: dict[str, Any]
