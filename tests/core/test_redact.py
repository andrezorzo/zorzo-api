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


def test_a_key_written_as_html_entities_is_found_after_the_scrub_decodes_it() -> None:
    redacted = REDACTOR.text("a&amp;#64;b.com e sk&amp;#45;abcdefghijklmnop")
    assert "abcdefghijklmnop" not in redacted and "[email]" in redacted


def test_a_configured_secret_written_as_html_entities_is_found() -> None:
    encoded = "".join(f"&#{ord(c)};" for c in SECRET)  # shows as the secret on a page
    assert SECRET not in REDACTOR.text(f"seed {encoded} end") and "[secret]" in REDACTOR.text(encoded)


@pytest.mark.parametrize(
    "text",
    [
        "https://zorzo.dev.br/blog/building-a-rag-pipeline-in-2026-part-2",
        "see https://zorzo.dev.br/blog/building-a-rag-pipeline-in-2026-part-2, then",
        "building-a-rag-pipeline-in-2026-part-2-with-citations",  # a slug on its own
    ],
)
def test_a_url_or_slug_is_not_a_key(text: str) -> None:
    assert REDACTOR.text(text) == text


def test_a_uuid_format_key_is_redacted() -> None:
    # Nothing configures it, and a UUID is as good a key as any other: only words and URLs are spared.
    assert REDACTOR.text("trace 550e8400-e29b-41d4-a716-446655440000 ended") == "trace [secret] ended"


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/services/T0123/B4567/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        "https://api.example.com/v1/items?id=7&api_key=a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4&lang=en",
    ],
)
def test_a_key_in_a_url_path_or_query_is_found(url: str) -> None:
    redacted = REDACTOR.text(f"call {url} now")
    assert "a1b2c3d4e5f6" not in redacted and redacted.startswith("call https://") and "[secret]" in redacted


@pytest.mark.parametrize(
    ("secret", "text"),
    [
        (
            "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
            "bot 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw ok",
        ),
        ("S3cret!11987654321", "pass S3cret!11987654321 ok"),
        ("mypass-sk-abcdefgh12", "pass mypass-sk-abcdefgh12 ok"),
        ("Zx9!joao@mail.com", "pass Zx9!joao@mail.com ok"),
        ("Senha²º!forte", "pass Senha²º!forte ok"),  # NFKC reads it as `Senha2o!forte`
    ],
)
def test_a_configured_secret_is_replaced_whole_even_when_it_holds_other_matches(
    secret: str, text: str
) -> None:
    redacted = Redactor(secrets=[secret]).text(text)
    assert redacted == text.replace(secret, "[secret]")


def test_the_telegram_bot_token_is_found_without_being_configured() -> None:
    assert REDACTOR.text("bot 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw ok") == "bot [secret] ok"


def test_an_allowlisted_address_with_a_compatibility_character_passes() -> None:
    redactor = Redactor(allowlist=["contatoº@zorzo.dev.br"])
    assert redactor.text("write to contatoº@zorzo.dev.br or joao@gmail.com") == (
        "write to contatoo@zorzo.dev.br or [email]"  # the canonical (NFKC) form
    )


def test_an_allowlisted_address_is_cut_where_a_secret_lies_in_it() -> None:
    redactor = Redactor(allowlist=["contatoº@zorzo.dev.br"], secrets=["zorzo"])
    assert redactor.text("contatoº@zorzo.dev.br") == "contatoo@[secret].dev.br"


def test_a_long_secret_is_found_in_any_case() -> None:
    canary = "Canary-0123456789-abcdefghijKLMNOPQR"  # 32 characters or more
    assert Redactor(secrets=[canary]).text(f"say {canary.upper()} and {canary.lower()}") == (
        "say [secret] and [secret]"
    )
    assert Redactor(secrets=["Short-Secret"]).text("short-secret") == "short-secret"  # case matters below it


def test_entities_are_decoded_in_the_stored_text() -> None:
    assert REDACTOR.text("Tom &amp; Jerry") == "Tom & Jerry"


def test_a_short_secret_inside_a_longer_token_leaves_none_of_it() -> None:
    redactor = Redactor(secrets=["123456789"])
    assert redactor.text("id 12345678901234567890abcdefABCDEF12 end") == "id [secret] end"


def test_a_short_numeric_secret_inside_a_phone_number_leaves_none_of_it() -> None:
    redacted = Redactor(secrets=["987654"]).text("call +55 11 98765-4321 or 987654")
    assert "98765" not in redacted and "4321" not in redacted


def test_fullwidth_characters_do_not_hide_an_email() -> None:
    fullwidth_at_sign = "\uff20"
    assert REDACTOR.text(f"joao.silva{fullwidth_at_sign}gmail.com") == "[email]"
