"""ADR 0011, F01-T06 and F01-T14: the intents, what each one does and how `route` learns them come from the
domain package, not the core."""

import re
from importlib.resources import files
from typing import get_args

import pytest
from pydantic import ValidationError
from rag_core.domain import DomainError, Intent, check_domain, load_domain, load_intents, load_prompts
from rag_core.services.route import route_schema

from tests.conftest import DomainFactory, named_in_core

ROUTED = {"standalone_question": "q", "language": "en", "follow_up": False, "public_ok": True}
ZORZO = load_domain("zorzo")
ZORZO_KINDS = ZORZO.document_kinds
CONTACT = ZORZO.contact
STAGES = ("route", "assess", "reformulate", "answer", "judge", "repair")


def prompt_files(route: str) -> dict[str, str]:
    """The six prompt files, keyed for `make_domain`, with the given `route.md`."""
    return {f"prompts__{stage}.md": f"{stage} prompt\n" for stage in STAGES} | {"prompts__route.md": route}


def test_the_domain_declares_its_intents_and_their_rules() -> None:
    intents = load_intents("zorzo", ZORZO_KINDS)
    assert intents["career_fact"].kinds == ("faq",)
    assert intents["off_topic"].action == "decline"
    assert intents["about_andre"].kinds == () and intents["about_andre"].action == "answer"


def test_the_domain_declares_its_contact_options_and_link_hosts() -> None:
    assert [(o.label, o.url) for o in CONTACT] == [
        ("LinkedIn", "https://www.linkedin.com/in/andrezorzo"),
        ("GitHub", "https://github.com/andrezorzo"),
        ("Email", "mailto:andre_zorzo@yahoo.com"),
    ]
    assert ZORZO.link_hosts == ("zorzo.dev.br", "github.com", "linkedin.com")
    assert load_intents("zorzo", ZORZO_KINDS)["contact"].action == "contact"


def test_a_contact_intent_needs_contact_options(make_domain: DomainFactory) -> None:
    name = make_domain(
        "nocontact", {"intents.yaml": "reach: {action: contact, description: Reach him.}\n"}, from_sample=True
    )
    intents = load_intents(name, ("faq",))
    with pytest.raises(DomainError, match="contact"):
        check_domain(load_domain(name), intents)  # the sample domain declares no contact options
    check_domain(ZORZO, load_intents("zorzo", ZORZO_KINDS))


def test_route_accepts_only_the_domains_intents() -> None:
    schema = route_schema(("billing", "other"))
    assert schema.model_validate({**ROUTED, "intent": "billing"}).intent == "billing"
    assert schema.model_json_schema()["properties"]["intent"]["enum"] == ["billing", "other"]
    with pytest.raises(ValidationError):
        schema.model_validate({**ROUTED, "intent": "career_fact"})


def test_the_core_names_no_domain_intent() -> None:
    # `contact` is also the name of an action (the core's own vocabulary), so only the other names count.
    intents = load_intents("zorzo", ZORZO_KINDS)
    assert named_in_core(set(intents) - set(get_args(Intent.model_fields["action"].annotation))) == {}


def test_an_intent_reads_the_domains_own_kinds(make_domain: DomainFactory) -> None:
    name = make_domain("billing", {"intents.yaml": "invoices: {kinds: [invoice], description: Invoices.}\n"})
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
def test_bad_intents_stop_the_app(make_domain: DomainFactory, name: str, yaml_text: str, error: str) -> None:
    make_domain(name, {"intents.yaml": yaml_text})
    with pytest.raises(DomainError, match=error):
        load_intents(name, ("faq",))


def test_the_route_prompt_lists_the_domains_intents(make_domain: DomainFactory) -> None:
    name = make_domain("listed", prompt_files("Classify.\n$intents\nReturn JSON.\n"))
    intents = {
        "invoices": Intent(description="Questions about invoices."),
        "other": Intent(description="Anything else."),
    }
    route = load_prompts(name, intents).route
    assert route == (
        "Classify.\n- `invoices`: Questions about invoices.\n- `other`: Anything else.\nReturn JSON.\n"
    )


@pytest.mark.parametrize(
    ("name", "route"),
    [
        ("unslotted", "Classify into: a, b.\n"),  # the intents would never reach the model
        (
            "overslotted",
            "Classify.\n$intents\nThe question is $question.\n",
        ),  # sent as `$question`, literally
    ],
)
def test_a_route_prompt_without_exactly_the_intents_slot_stops_the_app(
    make_domain: DomainFactory, name: str, route: str
) -> None:
    make_domain(name, prompt_files(route))
    with pytest.raises(DomainError, match="intents"):
        load_prompts(name, {"a": Intent(description="A.")})


def test_the_prompts_name_no_intent() -> None:
    names = load_intents("zorzo", ZORZO_KINDS)
    for path in files("domain_zorzo").joinpath("prompts").iterdir():
        text = path.read_text()
        assert not [n for n in names if re.search(rf"\b{re.escape(n)}\b", text)], path.name
