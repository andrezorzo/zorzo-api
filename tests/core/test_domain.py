"""F01-T14 AC1 and AC3: the document kinds and the languages are the domain's; the core names none of them."""

import pytest
from rag_core.domain import DomainError, load_domain, load_examples

from tests.conftest import DomainFactory, named_in_core

ZORZO = load_domain("zorzo")


def test_the_core_names_no_document_kind() -> None:
    assert named_in_core(set(ZORZO.document_kinds)) == {}


def test_the_core_names_no_language() -> None:
    assert named_in_core(set(ZORZO.languages) | set(ZORZO.languages.values())) == {}


def test_an_example_question_in_a_language_the_domain_doesnt_declare_stops_the_ingest(
    make_domain: DomainFactory,
) -> None:
    name = make_domain(
        "frenchless",
        {
            "domain.yaml": "name: frenchless\nlanguages: {en: english}\ndocument_kinds: [faq]\n",
            "examples.yaml": "- {id: fr-1, lang: fr, text: Bonjour ?}\n",
        },
        from_sample=True,
    )
    with pytest.raises(DomainError, match="fr"):
        load_examples(name, load_domain(name).languages)
