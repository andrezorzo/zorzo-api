"""ADR 0011, F01-T06: the intents and what each one does come from the domain package, not the core."""

import re
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from rag_core.domain import DomainError, load_intents
from rag_core.services.route import route_schema

CORE = Path(__file__).resolve().parents[2] / "packages/core/src/rag_core"
ROUTED = {"standalone_question": "q", "language": "en", "follow_up": False, "public_ok": True}


def test_the_domain_declares_its_intents_and_their_rules() -> None:
    intents = load_intents("zorzo")
    assert intents["career_fact"].kinds == ("faq",)
    assert intents["off_topic"].action == "decline"
    assert intents["about_andre"].kinds == () and intents["about_andre"].action == "answer"


def test_route_accepts_only_the_domains_intents() -> None:
    schema = route_schema(("billing", "other"))
    assert schema.model_validate({**ROUTED, "intent": "billing"}).intent == "billing"
    assert schema.model_json_schema()["properties"]["intent"]["enum"] == ["billing", "other"]
    with pytest.raises(ValidationError):
        schema.model_validate({**ROUTED, "intent": "career_fact"})


def test_the_core_names_no_domain_intent() -> None:
    names = load_intents("zorzo")
    for path in CORE.rglob("*.py"):
        text = path.read_text()
        assert not [n for n in names if re.search(rf"[\"']{re.escape(n)}[\"']", text)], path


@pytest.mark.parametrize(
    ("name", "yaml_text", "error"),
    [
        ("misspelled", "career_fact: {kinds: [faqs]}\n", "faqs"),  # a misspelled kind
        ("keyword", "no: {}\nother: {}\n", "must be strings"),  # a bare YAML keyword read as a boolean
    ],
    ids=["misspelled", "keyword"],
)
def test_bad_intents_stop_the_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    name: str,
    yaml_text: str,
    error: str,
) -> None:
    package = tmp_path / f"domain_{name}"  # one package per case: imports are cached by name
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "intents.yaml").write_text(yaml_text)
    monkeypatch.syspath_prepend(str(tmp_path))
    request.addfinalizer(lambda: sys.modules.pop(f"domain_{name}", None))  # imported from this tmp_path
    with pytest.raises(DomainError, match=error):
        load_intents(name)
