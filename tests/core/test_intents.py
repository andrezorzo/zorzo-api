"""ADR 0011, F01-T06: the intents and what each one does come from the domain package, not the core."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from rag_core.domain import load_intents
from rag_core.services.route import route_schema

CORE = Path(__file__).resolve().parents[2] / "packages/core/src/rag_core"
ROUTED = {"standalone_question": "q", "language": "en", "follow_up": False, "public_ok": True}


def test_the_domain_declares_its_intents_and_their_rules() -> None:
    intents = load_intents("zorzo")
    assert intents["career_fact"].kinds == ("faq",)
    assert intents["off_topic"].action == "decline"
    assert intents["about_andre"].kinds == () and intents["about_andre"].action == "answer"


def test_route_accepts_only_the_domains_intents() -> None:
    schema = route_schema(["billing", "other"])
    assert schema.model_validate({**ROUTED, "intent": "billing"}).intent == "billing"
    assert schema.model_json_schema()["properties"]["intent"]["enum"] == ["billing", "other"]
    with pytest.raises(ValidationError):
        schema.model_validate({**ROUTED, "intent": "career_fact"})


def test_the_core_names_no_domain_intent() -> None:
    names = load_intents("zorzo")
    for path in CORE.rglob("*.py"):
        text = path.read_text()
        assert not [n for n in names if f'"{n}"' in text], path
