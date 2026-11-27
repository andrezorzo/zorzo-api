"""AC6: the ordering checker every stream test runs rejects each broken rule of LLD "Stream events"."""

from typing import Any

import pytest

from tests.contract.sse import Event, check_ordering

DONE: dict[str, Any] = {"trace_id": "t", "outcome": "answered", "cached": False, "suggestion": None}


def _events(*names: str, done: dict[str, Any] | None = None, cost: float = 0.0) -> list[Event]:
    out = []
    for name in names:
        if name == "done":
            out.append(Event("done", done or DONE))
        elif name == "usage":
            out.append(Event("usage", {"cost_usd": cost}))
        else:
            out.append(Event(name, {}))
    return out


def test_accepts_a_search_only_stream() -> None:
    check_ordering(_events("stage", "stage", "degraded", "retrieval", "done"))


def test_accepts_a_verified_answer() -> None:
    check_ordering(_events("stage", "retrieval", "token", "token", "claim", "claim", "usage", "done"))


@pytest.mark.parametrize(
    ("names", "rule"),
    [
        (("stage", "retrieval"), "exactly once"),
        (("stage", "done", "done"), "exactly once"),
        (("done", "stage"), "last"),
        (("blocked", "stage", "done"), "followed only by done"),
        (("error", "retrieval", "done"), "followed only by done"),
        (("degraded", "stage", "done"), "followed only by retrieval"),
        (("token", "claim", "token", "done"), "no token after the first claim"),
    ],
)
def test_rejects_a_broken_rule(names: tuple[str, ...], rule: str) -> None:
    with pytest.raises(AssertionError, match=rule):
        check_ordering(_events(*names))


def test_rejects_a_cached_answer_with_tokens_or_cost() -> None:
    cached = {**DONE, "cached": True}
    with pytest.raises(AssertionError, match="no token"):
        check_ordering(_events("stage", "token", "claim", "usage", "done", done=cached))
    with pytest.raises(AssertionError, match="costs 0"):
        check_ordering(_events("stage", "claim", "usage", "done", done=cached, cost=0.001))
    check_ordering(_events("stage", "claim", "usage", "done", done=cached))
