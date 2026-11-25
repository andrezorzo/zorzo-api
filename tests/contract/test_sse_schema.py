"""AC6: the committed stream contract is the one the event models generate, and covers every F01 event."""

import json

from rag_core.events import sse_schema

from tests.contract.sse import SCHEMA_PATH

# Written out from LLD "Stream events" and the spec's API contract, not derived from the models.
F01_EVENTS = {
    "stage",
    "guard",
    "retrieval",
    "model",
    "token",
    "claim",
    "usage",
    "blocked",
    "degraded",
    "error",
    "done",
}


def test_committed_schema_matches_the_event_models() -> None:
    committed = json.loads(SCHEMA_PATH.read_text())
    assert committed == sse_schema(), "regenerate with: uv run python -m rag_core.events"


def test_schema_covers_every_f01_event() -> None:
    committed = json.loads(SCHEMA_PATH.read_text())
    defs = committed["$defs"]
    names = {
        defs[ref["$ref"].rsplit("/", 1)[1]]["properties"]["event"]["const"] for ref in committed["oneOf"]
    }
    assert names == F01_EVENTS
