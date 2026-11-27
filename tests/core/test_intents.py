"""ADR 0011, F01-T06 and F01-T14: the intents, what each one does and how `route` learns them come from the
domain package, not the core."""

import re
import sys
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

import pytest
from pydantic import ValidationError
from rag_core.domain import DomainError, Intent, load_domain, load_intents, load_prompts
from rag_core.services.route import route_schema

CORE = Path(__file__).resolve().parents[2] / "packages/core/src/rag_core"
ROUTED = {"standalone_question": "q", "language": "en", "follow_up": False, "public_ok": True}
ZORZO_KINDS = load_domain("zorzo").document_kinds
STAGES = ("route", "assess", "reformulate", "answer", "judge", "repair")

MakeDomain = Callable[..., str]


@pytest.fixture
def make_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> MakeDomain:
    """A throwaway `domain_<name>` package with the given files (relative path → text)."""
    monkeypatch.syspath_prepend(str(tmp_path))

    def make(name: str, **texts: str) -> str:
        package = tmp_path / f"domain_{name}"  # one package per name: imports are cached by name
        package.mkdir()
        (package / "__init__.py").write_text("")
        for relative, text in texts.items():
            path = package / relative.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        request.addfinalizer(lambda: sys.modules.pop(f"domain_{name}", None))  # imported from this tmp_path
        return name

    return make


def prompt_files(route: str) -> dict[str, str]:
    """The six prompt files, keyed for `make_domain`, with the given `route.md`."""
    return {f"prompts__{stage}.md": f"{stage} prompt\n" for stage in STAGES} | {"prompts__route.md": route}


def test_the_domain_declares_its_intents_and_their_rules() -> None:
    intents = load_intents("zorzo", ZORZO_KINDS)
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
    names = load_intents("zorzo", ZORZO_KINDS)
    for path in CORE.rglob("*.py"):
        text = path.read_text()
        assert not [n for n in names if re.search(rf"[\"']{re.escape(n)}[\"']", text)], path


def test_an_intent_reads_the_domains_own_kinds(make_domain: MakeDomain) -> None:
    name = make_domain(
        "billing", **{"intents.yaml": "invoices: {kinds: [invoice], description: Invoices.}\n"}
    )
    assert load_intents(name, ("invoice",))["invoices"].kinds == ("invoice",)


@pytest.mark.parametrize(
    ("name", "yaml_text", "error"),
    [
        # a kind the domain doesn't declare (a misspelling, or another domain's kind)
        ("misspelled", "career_fact: {kinds: [faqs], description: Facts.}\n", "faqs"),
        # a bare YAML keyword read as a boolean
        ("keyword", "no: {description: No.}\nother: {description: Other.}\n", "must be strings"),
        ("undescribed", "other: {}\n", "description"),  # route could not tell the model what it means
    ],
    ids=["misspelled", "keyword", "undescribed"],
)
def test_bad_intents_stop_the_app(make_domain: MakeDomain, name: str, yaml_text: str, error: str) -> None:
    make_domain(name, **{"intents.yaml": yaml_text})
    with pytest.raises(DomainError, match=error):
        load_intents(name, ("faq",))


def test_the_route_prompt_lists_the_domains_intents(make_domain: MakeDomain) -> None:
    name = make_domain("listed", **prompt_files("Classify.\n$intents\nThe question is $question.\n"))
    intents = {
        "invoices": Intent(description="Questions about invoices."),
        "other": Intent(description="Anything else."),
    }
    route = load_prompts(name, intents).route
    assert route == (
        "Classify.\n- `invoices`: Questions about invoices.\n- `other`: Anything else.\n"
        "The question is $question.\n"  # the nodes' own slots are left for them
    )


def test_a_route_prompt_without_the_intents_slot_stops_the_app(make_domain: MakeDomain) -> None:
    name = make_domain("unslotted", **prompt_files("Classify into: a, b.\n"))
    with pytest.raises(DomainError, match="intents"):
        load_prompts(name, {"a": Intent(description="A.")})


def test_the_prompts_name_no_intent() -> None:
    names = load_intents("zorzo", ZORZO_KINDS)
    for path in files("domain_zorzo").joinpath("prompts").iterdir():
        text = path.read_text()
        assert not [n for n in names if re.search(rf"\b{re.escape(n)}\b", text)], path.name
