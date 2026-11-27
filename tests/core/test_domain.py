"""F01-T14 AC1 and AC3: the document kinds and the languages are the domain's; the core names none of them."""

import re
import sys
from pathlib import Path

import pytest
from rag_core.domain import DomainError, load_domain, load_examples

from tests.conftest import copy_domain

CORE = Path(__file__).resolve().parents[2] / "packages/core/src/rag_core"
ZORZO = load_domain("zorzo")


def named_in_core(names: set[str]) -> dict[str, list[str]]:
    """The given names written as a quoted string in a core source file, by file."""
    found: dict[str, list[str]] = {}
    for path in CORE.rglob("*.py"):
        text = path.read_text()
        hits = sorted(n for n in names if re.search(rf"[\"']{re.escape(n)}[\"']", text))
        if hits:
            found[str(path.relative_to(CORE))] = hits
    return found


def test_the_core_names_no_document_kind() -> None:
    assert named_in_core(set(ZORZO.document_kinds)) == {}


def test_the_core_names_no_language() -> None:
    assert named_in_core(set(ZORZO.languages) | set(ZORZO.languages.values())) == {}


def test_an_example_question_in_a_language_the_domain_doesnt_declare_stops_the_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    domains = copy_domain(
        tmp_path, "frenchless", "name: frenchless\nlanguages: {en: english}\ndocument_kinds: [faq]\n"
    )
    (domains / "domain_frenchless" / "examples.yaml").write_text("- {id: fr-1, lang: fr, text: Bonjour ?}\n")
    monkeypatch.syspath_prepend(str(domains))
    request.addfinalizer(lambda: sys.modules.pop("domain_frenchless", None))
    domain = load_domain("frenchless")
    with pytest.raises(DomainError, match="fr"):
        load_examples("frenchless", domain.languages)
