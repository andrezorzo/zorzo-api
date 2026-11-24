"""AC1: the gate's PII pass, front matter parsing and secret scan, each in isolation."""

from pathlib import Path

import pytest
from rag_core.adapters.gitleaks import GitleaksError, GitleaksScanner
from rag_core.ingest.frontmatter import FrontMatterError, parse
from rag_core.ingest.gate import check, personal_data
from rag_core.ports import Finding

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


@pytest.mark.parametrize(
    "text",
    [
        "![logo](img/logo@2x.png)",
        "logo@2x-dark.png",
        "logo@1.5x.png",
        "favicon@2x.ico",
        "pkg@1.0.0-beta.rc",
        "pkg@1.0.0+build.5",
    ],
)
def test_leaves_asset_names_and_versions_alone(text: str) -> None:
    assert personal_data(text) == []


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


@pytest.mark.parametrize(
    "text",
    [
        "[cv](files/joao.silva@gmail.com.pdf)",
        "joao@gmail.com-cv.pdf",
        "ana@mail.zip.com.br",
        "ana@studio.gif-art.com",
        "ana@3x.com.br",
        "joe@2x.io",
        "x@1.2.example.com",
        "x@1.2.3.example.com",
        "![](img/joe@2x.io.png)",
        # Fails closed: a retina name with an extra label can't be told from an address in a file name.
        "icon@2x.dark.png",
        "user@host@example.com",
        "_joao@gmail.com_",
    ],
)
def test_finds_emails_that_look_like_asset_names(text: str) -> None:
    assert personal_data(text) == ["pii:email"]


def test_finds_emails_whose_domain_starts_with_a_digit() -> None:
    assert personal_data("Mail li@163.com or x@1und1.de or a@1password.com") == ["pii:email"]


@pytest.mark.parametrize(
    "text", ["Ligue 11 98765 4321.", "Ligue 11 987654321.", "Ligue 11 9 8765-4321.", "Ligue 11-98765-4321."]
)
def test_finds_brazilian_mobiles(text: str) -> None:
    assert "pii:phone" in personal_data(text)


@pytest.mark.parametrize(
    "text",
    [
        "+55\u00a011\u00a098765-4321",
        "(11)\u00a098765-4321",
        "contato: +55 (11)\n98765-4321",
        "(11)\n98765 4321",
    ],
)
def test_finds_phones_across_odd_spaces_and_a_wrapped_line(text: str) -> None:
    assert "pii:phone" in personal_data(text)


@pytest.mark.parametrize("text", ["issue 42\n91234-5678"])
def test_leaves_plain_numbers_alone(text: str) -> None:
    assert personal_data(text) == []


def test_an_impossible_date_is_a_front_matter_problem() -> None:
    with pytest.raises(FrontMatterError):
        parse("---\n" + VALID + "updated: 2024-13-45\n---\nbody\n")


def test_an_unknown_key_is_reported_without_its_name() -> None:
    with pytest.raises(FrontMatterError) as caught:
        parse("---\n" + VALID + "joao.silva@gmail.com: true\n---\nbody\n")
    assert "joao" not in str(caught.value)


def test_deeply_nested_front_matter_is_a_problem_not_a_crash() -> None:
    with pytest.raises(FrontMatterError):
        parse("---\nx: " + "[" * 5000 + "\n---\nbody\n")


class NoSecrets:
    async def scan(self, folder: Path) -> list[Finding]:
        return []


async def test_reads_a_file_saved_with_a_byte_order_mark(tmp_path: Path) -> None:
    (tmp_path / "bom.md").write_bytes(("﻿---\r\n" + VALID + "---\r\n# T\r\n\r\ntext\r\n").encode())
    documents, problems = await check(tmp_path, NoSecrets(), ())
    assert problems == [] and documents[0].meta.doc_key == "d"
