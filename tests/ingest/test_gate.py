"""AC1: the gate's PII pass, front matter parsing and secret scan, each in isolation."""

from pathlib import Path

import pytest
from rag_core.adapters.gitleaks import GitleaksError, GitleaksScanner
from rag_core.ingest.frontmatter import FrontMatterError, parse
from rag_core.ingest.gate import personal_data

VALID = "doc_key: d\ntitle: T\nkind: faq\nlang: en\nauthority: 1\nreviewed_by_owner: true\n"


@pytest.mark.parametrize(
    "text",
    [
        "Call +1 415 555 1234.",
        "Call (415) 555-1234.",
        "Ligue 11 98765-4321.",
        "Ligue (11) 98765-4321.",
        "Ligue +55 11 98765-4321.",
    ],
)
def test_finds_phone_numbers(text: str) -> None:
    assert "pii:phone" in personal_data(text)


@pytest.mark.parametrize("text", ["From 2015-2020 he led 12 teams.", "Version 1.2.3, port 8000."])
def test_leaves_years_and_numbers_alone(text: str) -> None:
    assert personal_data(text) == []


def test_finds_emails_but_not_asset_names() -> None:
    assert personal_data("Write to joao.silva@gmail.com") == ["pii:email"]
    assert personal_data("![logo](img/logo@2x.png)") == []


def test_allowlist_matches_regardless_of_case() -> None:
    assert personal_data("Andre_Zorzo@Yahoo.com", ["andre_zorzo@yahoo.com"]) == []


def test_parses_windows_line_endings_and_a_closing_line_at_the_end() -> None:
    meta, body = parse(("---\n" + VALID + "---\n# T\n\ntext\n").replace("\n", "\r\n"))
    assert meta.doc_key == "d" and "text" in body
    meta, body = parse("---\n" + VALID + "---")
    assert body == ""


@pytest.mark.parametrize("head", ["", "- a list\n", "just a string\n"])
def test_rejects_front_matter_that_is_not_a_mapping(head: str) -> None:
    with pytest.raises(FrontMatterError):
        parse(f"---\n{head}---\nbody\n")


async def test_a_failed_scan_is_an_error_not_a_clean_result(tmp_path: Path) -> None:
    with pytest.raises(GitleaksError):
        await GitleaksScanner().scan(tmp_path / "missing")
