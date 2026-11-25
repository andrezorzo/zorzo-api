"""Shared stream reader for every stream test (AC6).

It parses the SSE body, validates each event against the committed `contracts/sse-v1.schema.json` and
checks the ordering rules of LLD "Stream events", so every stream test is also a contract test.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "sse-v1.schema.json"


@dataclass(frozen=True)
class Event:
    name: str
    data: dict[str, Any]


@dataclass(frozen=True)
class Stream:
    events: list[Event]
    comments: list[str]

    def names(self) -> list[str]:
        return [e.name for e in self.events]

    def only(self, name: str) -> list[Event]:
        return [e for e in self.events if e.name == name]


@cache
def schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
    return loaded


def parse(body: str) -> Stream:
    events: list[Event] = []
    comments: list[str] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name, data = None, []
        for line in block.split("\n"):
            if line.startswith(":"):
                comments.append(line[1:].strip())
            elif line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].strip())
        if name is not None:
            events.append(Event(name, json.loads("\n".join(data))))
    return Stream(events, comments)


def validate(stream: Stream) -> None:
    for event in stream.events:
        jsonschema.validate({"event": event.name, "data": event.data}, schema())
    check_ordering(stream.events)


def check_ordering(events: list[Event]) -> None:
    """The ordering rules of LLD "Stream events"; raises AssertionError on the first broken rule."""
    names = [e.name for e in events]
    assert names.count("done") == 1, "done must be sent exactly once"
    assert names[-1] == "done", "done must be the last event"

    for i, name in enumerate(names):
        if name in ("blocked", "error"):
            assert names[i + 1 :] == ["done"], f"{name} must be followed only by done"
        if name == "degraded":
            rest = names[i + 1 :]
            assert set(rest[:-1]) <= {"retrieval"}, "degraded must be followed only by retrieval, then done"

    if "claim" in names:
        first_claim = names.index("claim")
        assert "token" not in names[first_claim:], "no token after the first claim"

    done = events[-1].data
    if done.get("cached"):
        assert "token" not in names, "a cached answer sends no token"
        usage = [e for e in events if e.name == "usage"]
        assert usage and all(u.data["cost_usd"] == 0 for u in usage), "a cached answer costs 0"


def read(body: str) -> Stream:
    stream = parse(body)
    validate(stream)
    return stream
