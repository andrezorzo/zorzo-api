"""F01-T07 AC4: answers aim at `ANSWER_WORDS_DEFAULT` words and reach `ANSWER_WORDS_MAX` on a request for more
detail; the answer prompt carries both, and a prompt without their slots stops the app."""

from pathlib import Path

import pytest
from rag_core.domain import DomainError, Intent, load_prompts
from rag_core.limits import load_limits
from rag_core.services.prompts import answer_messages, repair_messages

from tests.conftest import DomainFactory
from tests.core.test_intents import prompt_files

ROOT = Path(__file__).resolve().parents[2]
LIMITS = load_limits(ROOT / "config/limits.yaml")
INTENTS = {"any": Intent(description="Any question.")}
# LLD "9. Values and configuration": the target and the ceiling of an answer, in words.
WORDS_DEFAULT, WORDS_MAX = 150, 400


def test_the_answer_prompt_carries_the_length_targets() -> None:
    prompts = load_prompts("zorzo", INTENTS)
    system = answer_messages(prompts.answer, "Who is he?", language="en", passages="", limits=LIMITS)[0]
    repair = repair_messages(prompts.repair, prompts.answer, "{}", language="en", passages="", limits=LIMITS)[
        0
    ]
    for message in (system, repair):
        assert message["role"] == "system"
        assert f"{WORDS_DEFAULT} words" in message["content"]
        assert f"{WORDS_MAX} words" in message["content"]
        assert "$words" not in message["content"]


@pytest.mark.parametrize(
    ("name", "answer"),
    [
        ("nodefault", "Answer in $language, up to $words_max words.\n$passages\n"),
        ("nomax", "Answer in $language, about $words_default words.\n$passages\n"),
    ],
)
def test_an_answer_prompt_without_a_length_slot_stops_the_app(
    make_domain: DomainFactory, name: str, answer: str
) -> None:
    make_domain(name, {**prompt_files("Classify.\n$intents\n"), "prompts__answer.md": answer})
    with pytest.raises(DomainError, match="words_"):
        load_prompts(name, INTENTS)
