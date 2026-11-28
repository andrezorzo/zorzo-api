"""The conversation of a session (LLD "6.3 Agent run", conversation): what is kept of earlier turns, and what
`route` reads of it. No I/O: the graph's checkpointer stores it as plain types.

A turn is the standalone question and the text of the claims published for it. The last `HISTORY_TURNS`
turns are kept whole; older ones leave only their standalone question, in `earlier`, the oldest dropped first
past `HISTORY_SUMMARY_TOKENS`. `route` reads them as JSON within `HISTORY_MAX_TOKENS`.
"""

import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from rag_core.limits import Limits
from rag_core.services.gateway import estimate_tokens


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str  # the standalone question, redacted
    claims: list[str] = []  # the published claims' text (`supported`, `partial`), redacted


class Conversation(BaseModel):
    """The only value of the graph's state that is stored; `model_dump()` gives it as plain types."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    turns: list[Turn] = []  # oldest first, at most `HISTORY_TURNS`
    earlier: list[str] = []  # the standalone questions of older turns, oldest first
    updated_at: float = 0.0  # when the last turn began, in seconds since the epoch (UTC)


def _tokens(texts: Sequence[str]) -> int:
    return sum(estimate_tokens(len(text)) for text in texts)


def live(conversation: Conversation, now: float, ttl: float) -> Conversation:
    """The conversation, or an empty one when its last turn is older than `ttl` seconds."""
    return conversation if now - conversation.updated_at <= ttl else Conversation()


def begin_turn(conversation: Conversation, question: str, now: float, limits: Limits) -> Conversation:
    """The conversation with a new turn for `question`: turns past `HISTORY_TURNS` join the summary."""
    turns = [*conversation.turns, Turn(question=question)]
    cut = max(0, len(turns) - limits.history_turns)
    earlier = [*conversation.earlier, *(turn.question for turn in turns[:cut])]
    while earlier and _tokens(earlier) > limits.history_summary_tokens:
        earlier.pop(0)
    return Conversation(turns=turns[cut:], earlier=earlier, updated_at=now)


def with_claims(conversation: Conversation, claims: Sequence[str]) -> Conversation:
    """The conversation with the claims published for its last turn."""
    if not conversation.turns:
        return conversation
    last = conversation.turns[-1].model_copy(update={"claims": list(claims)})
    return conversation.model_copy(update={"turns": [*conversation.turns[:-1], last]})


def route_input(question: str, conversation: Conversation, limits: Limits) -> str:
    """`route`'s user message: the question and the history as JSON strings, which keep them data. Over
    `HISTORY_MAX_TOKENS` the summary goes first, then the oldest turn, then the last turn's claims from the
    end; the last turn's question always stays."""
    earlier = list(conversation.earlier)
    turns = [(turn.question, list(turn.claims)) for turn in conversation.turns]

    def size() -> int:
        return _tokens(earlier) + sum(_tokens([q, *claims]) for q, claims in turns)

    while size() > limits.history_max_tokens:
        if earlier:
            earlier.pop(0)
        elif len(turns) > 1:
            turns.pop(0)
        elif turns and turns[-1][1]:
            turns[-1][1].pop()
        else:
            break
    history = [{"question": q, "answer": " ".join(claims)} for q, claims in turns]
    return json.dumps({"question": question, "history": history, "earlier": earlier}, ensure_ascii=False)
