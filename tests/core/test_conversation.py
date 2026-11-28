"""F01-T07 AC1: what the conversation keeps of earlier turns, and what `route` reads of it (LLD "6.3 Agent
run", conversation)."""

import json
from pathlib import Path

from rag_core.limits import load_limits
from rag_core.services.conversation import Conversation, begin_turn, route_input, with_claims

ROOT = Path(__file__).resolve().parents[2]
LIMITS = load_limits(ROOT / "config/limits.yaml")  # 2 turns, a 250-token summary, an 800-token history
NOW = 1_800_000_000.0


def asked(*questions: str, claims: list[str] | None = None) -> Conversation:
    """A conversation of the given standalone questions, each answered with `claims`."""
    conversation = Conversation()
    for n, question in enumerate(questions):
        conversation = begin_turn(conversation, question, NOW + n, LIMITS)
        conversation = with_claims(conversation, claims or [f"Answer {n}."])
    return conversation


def test_history_keeps_the_last_turns() -> None:
    conversation = asked("Q1?", "Q2?", "Q3?", "Q4?", "Q5?")
    read = json.loads(route_input("And then?", conversation, LIMITS))
    assert read == {
        "question": "And then?",
        "history": [{"question": "Q4?", "answer": "Answer 3."}, {"question": "Q5?", "answer": "Answer 4."}],
        "earlier": ["Q1?", "Q2?", "Q3?"],
    }
    assert conversation.updated_at == NOW + 4


def test_a_turn_without_published_claims_keeps_its_question() -> None:
    conversation = begin_turn(asked("Q1?"), "Q2?", NOW + 1, LIMITS)  # abstained: no claims
    read = json.loads(route_input("And then?", conversation, LIMITS))
    assert read["history"] == [{"question": "Q1?", "answer": "Answer 0."}, {"question": "Q2?", "answer": ""}]


def test_a_new_conversation_has_no_history() -> None:
    assert json.loads(route_input("Who is he?", Conversation(), LIMITS)) == {
        "question": "Who is he?",
        "history": [],
        "earlier": [],
    }


def test_older_questions_become_a_bounded_summary() -> None:
    # 75 characters each, 25 tokens (three characters a token): the 250-token summary holds ten.
    old = [f"{n:02d}" + "x" * 72 + "?" for n in range(14)]
    conversation = asked(*old, "Q-last-but-one?", "Q-last?")
    assert conversation.earlier == old[4:]  # the oldest went first


def test_the_route_input_stays_within_the_history_budget() -> None:
    # Claims of 300 characters (100 tokens): two turns of five are 1000 tokens, past the 800 budget.
    claims = [f"{n}" + "y" * 299 for n in range(5)]
    conversation = asked("Old question?", "Q-first?", "Q-last?", claims=claims)
    read = json.loads(route_input("And then?", conversation, LIMITS))
    assert read["earlier"] == []  # the summary went first
    assert read["history"] == [{"question": "Q-last?", "answer": " ".join(claims)}]  # then the oldest turn
    huge = [f"{n}" + "z" * 749 for n in range(4)]  # 250 tokens each: the last turn alone is past the budget
    read = json.loads(route_input("And then?", asked("Q-last?", claims=huge), LIMITS))
    assert read["history"] == [{"question": "Q-last?", "answer": " ".join(huge[:3])}]  # claims from the end
    giant = asked("Q-last?", claims=["w" * 4000])
    assert json.loads(route_input("And then?", giant, LIMITS))["history"] == [
        {"question": "Q-last?", "answer": ""}
    ]  # the last question always stays
