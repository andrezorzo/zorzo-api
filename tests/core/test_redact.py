"""F01-T10 AC3 (unit): the redactor every stored or exported trace passes through."""

import json
from typing import Any

import pytest
from rag_core.services.redact import Redactor

ALLOWED_EMAIL = "andre_zorzo@yahoo.com"
SECRET = "s3cr3t-salt-seed-0123456789-abcdef"
REDACTOR = Redactor(allowlist=[ALLOWED_EMAIL], secrets=[SECRET])
KEY_SHAPES = [
    "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef",
    "gsk_abcdefghijklmnopqrstuvwxyz0123456789",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "AIzaSyA-abcdefghijklmnopqrstuvwxyz01234",
    "lsv2_pt_" + "0123456789abcdef0123456789abcdef_0123456789",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    "-----BEGIN RSA PRIVATE KEY-----",
]


@pytest.mark.parametrize(
    ("text", "placeholder"),
    [
        ("Write to joao.silva@gmail.com now", "[email]"),
        ("Call +55 11 98765-4321 today", "[phone]"),
        ("My CPF is 529.982.247-25.", "[cpf]"),
        ("Card 4111 1111 1111 1111 please", "[card]"),
    ],
)
def test_personal_data_becomes_its_placeholder(text: str, placeholder: str) -> None:
    redacted = REDACTOR.text(text)
    assert placeholder in redacted
    assert not any(secret in redacted for secret in ("joao.silva", "98765", "529.982", "4111"))


def test_text_around_a_match_is_kept() -> None:
    assert REDACTOR.text("Mail joao@gmail.com, thanks") == "Mail [email], thanks"


def test_an_email_split_by_a_zero_width_character_is_still_found() -> None:
    redacted = REDACTOR.text("joao.si​lva@gmail.com")
    assert redacted == "[email]"


def test_hidden_characters_are_removed_but_whitespace_stays() -> None:
    assert REDACTOR.text("a​b‮c\x00d\ne\tf") == "abcd\ne\tf"


def test_the_allowlist_passes() -> None:
    assert (
        REDACTOR.text(f"Write to {ALLOWED_EMAIL} or joao@gmail.com") == f"Write to {ALLOWED_EMAIL} or [email]"
    )


def test_ordinary_numbers_are_not_personal_data() -> None:
    text = "From 2015-2020 he led 12 teams on port 8000, version 1.2.3, card 1234 5678 9012 3456"
    assert REDACTOR.text(text) == text  # no valid phone, CPF or card among them


def test_a_secret_value_is_replaced_wherever_it_appears() -> None:
    assert REDACTOR.text(f"it is {SECRET}, really; {SECRET}") == "it is [secret], really; [secret]"


def test_a_blank_secret_is_ignored() -> None:
    assert Redactor(secrets=["", "  "]).text("plain text") == "plain text"


def test_extra_secret_values_are_added_by_the_caller() -> None:
    canary = "canary-0000-1111"  # what a later task hands over with the settings' other secrets
    assert Redactor(secrets=[SECRET, canary]).text(f"say {canary}") == "say [secret]"


@pytest.mark.parametrize("shape", KEY_SHAPES)
def test_anything_shaped_like_a_key_is_replaced(shape: str) -> None:
    assert REDACTOR.text(f"the key {shape} leaked") == "the key [secret] leaked"


def test_a_run_of_32_letters_and_digits_is_replaced() -> None:
    assert REDACTOR.text("token a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4 end") == "token [secret] end"
    assert REDACTOR.text("token " + "a1" * 15 + "xy_-+= end") == "token [secret] end"


@pytest.mark.parametrize(
    "text",
    [
        "Internationalization" * 3,  # a long word: letters only
        "https://zorzo.dev.br/dash/evals/latest-nightly-run-results-for-the-english-set?page=2",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d",  # 31
        "0123456789" * 4,  # digits only
    ],
)
def test_a_long_word_or_a_url_is_not_a_secret(text: str) -> None:
    assert REDACTOR.text(text) == text


def test_the_whole_payload_is_walked() -> None:
    payload: dict[str, Any] = {
        "question": "mail joao@gmail.com",
        "events": [
            {"kind": "claim", "i": 0, "text": f"uses {SECRET}", "sources": [1, 2], "verdict": "supported"},
            {"kind": "retrieval", "hits": [{"id": 3, "title": "call +55 11 98765-4321", "score": 0.5}]},
        ],
        "cut": ("rerank",),
        "done": {"cached": False, "suggestion": None, "contact": {"email": "joao@gmail.com"}},
    }
    redacted = REDACTOR.walk(payload)
    dumped = json.dumps(redacted)
    for leaked in ("joao@gmail.com", SECRET, "98765"):
        assert leaked not in dumped
    assert redacted["events"][0]["sources"] == [1, 2] and redacted["events"][0]["verdict"] == "supported"
    assert redacted["events"][1]["hits"][0]["score"] == 0.5
    assert redacted["done"]["suggestion"] is None and redacted["done"]["cached"] is False
    assert payload["question"] == "mail joao@gmail.com"  # the input is left as it was


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ("mail joao&#64;gmail.com", "joao"),  # written as an HTML entity
        ("call +55 11\n98765-4321 now", "98765"),  # a line wrapped inside the number
    ],
)
def test_personal_data_hidden_from_the_plain_text_is_found(text: str, leaked: str) -> None:
    assert leaked not in REDACTOR.text(text)


def test_a_lone_surrogate_is_removed() -> None:
    assert REDACTOR.text("a\ud800b") == "ab"  # JSON lets it through, UTF-8 can't encode it
