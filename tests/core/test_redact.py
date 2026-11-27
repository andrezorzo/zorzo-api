"""F01-T10 AC3 (unit): the redactor every stored or exported trace passes through."""

import json
import time
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


# -- review round 3 --------------------------------------------------------------------------------------


def test_an_allowlist_search_keeps_the_positions_of_the_text() -> None:
    # Lowercasing `İ` makes two characters of one; the positions of what follows must still be the text's.
    for separator in ("", " "):  # glued to the address (another address) or apart from it
        text = "x " + "İ" * 21 + separator + "andre_zorzo@yahoo.com victim@example.com"
        redacted = REDACTOR.text(text)
        assert "victim" not in redacted
        assert ("andre_zorzo@yahoo.com" in redacted) == (separator == " ")


def test_a_secret_written_with_entity_like_text_is_found() -> None:
    secret = "Pw&amp;copy9xyzQ"
    redactor = Redactor(secrets=[secret])
    assert redactor.text(f"it is {secret}") == "it is [secret]"  # as the secret is written
    assert redactor.text("it is Pw©" + "9xyzQ") == "it is [secret]"  # as a page shows it


def test_a_phone_inside_an_escaped_tag_is_found() -> None:
    redacted = REDACTOR.text("call &amp;lt;b (11) 98765-4321&amp;gt; now")
    assert "98765" not in redacted and "4321" not in redacted and "[phone]" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "KEY=a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4=",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4===",
        "export TOKEN=a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4==\nnext",
    ],
)
def test_a_token_followed_by_equals_signs_is_found(text: str) -> None:
    redacted = REDACTOR.text(text)
    assert "a1b2c3d4e5f6" not in redacted and "[secret]" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/blog/2024-11-desenvolvimento-de-software-com-python",
        "/0001-use-python3-for-the-backend-service.md",
        "https://zorzo.dev.br/blog/building-a-rag-pipeline-in-2026-part-2",
        "2024-11-a-very-long-title-about-the-internationalization-of-everything",
    ],
)
def test_slugs_do_not_depend_on_word_length(text: str) -> None:
    assert REDACTOR.text(text) == text


def test_a_key_with_few_parts_or_cut_in_groups_is_still_a_key() -> None:
    assert REDACTOR.text("SKLIVEABCDEFGHIJKLMNOPQRSTUV_12345678901234") == "[secret]"
    assert REDACTOR.text("a1b2c3d4-e5f6g7h8-i9j0k1l2-m3n4o5p6") == "[secret]"  # all groups mixed


def test_two_secrets_that_overlap_are_both_cut() -> None:
    redactor = Redactor(secrets=["abcd1234", "1234wxyz"])
    assert redactor.text("pass abcd1234wxyz end") == "pass [secret] end"


def test_a_base64_key_with_slashes_is_found_outside_a_url() -> None:
    key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    assert REDACTOR.text(f"secret {key} end") == "secret [secret] end"
    assert REDACTOR.text(f"see https://example.com/docs/{key.replace('/', '-')} now").count("[secret]") == 0


def test_a_whole_pem_block_is_replaced() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234\nabcdEFGH\n-----END RSA PRIVATE KEY-----"
    assert REDACTOR.text(f"key:\n{pem}\nthen") == "key:\n[secret]\nthen"


def test_a_text_that_decodes_past_the_layer_limit_is_replaced_whole() -> None:
    nested = "contact &amp;amp;amp;amp;#64; me"  # one layer more than the redactor decodes
    assert REDACTOR.text(nested) == "[redacted]"
    assert REDACTOR.text("Tom &amp;amp; Jerry") == "Tom & Jerry"  # within the limit


# -- review round 4 ----------------------------------------------------------------------------------------

AWS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


@pytest.mark.parametrize(
    "text",
    [
        "/api/v1/users/12345/profile/settings",
        "/usr/lib/python3/site/packages/numpy/core",
        "src/rag_core/services/v2/handlers/authentication.py",
        "2024/11/27/building/a/rag/pipeline/in/python3",
    ],
)
def test_a_path_is_not_a_base64_key(text: str) -> None:
    assert REDACTOR.text(text) == text
    assert REDACTOR.text(f"open {text} now") == f"open {text} now"


def test_a_key_after_a_url_or_in_its_query_is_found() -> None:
    assert REDACTOR.text(f"https://example.com/cb?sig={AWS_KEY}") == "https://example.com/cb?sig=[secret]"
    assert REDACTOR.text(f"https://example.com/x,{AWS_KEY}") == "https://example.com/x,[secret]"
    assert REDACTOR.text(f"https://example.com/p#frag={AWS_KEY}") == "https://example.com/p#frag=[secret]"


def test_a_private_key_header_without_an_end_is_cut_alone_and_prose_between_blocks_stays() -> None:
    assert REDACTOR.text("see -----BEGIN RSA PRIVATE KEY----- here") == "see [secret] here"
    two = (
        "-----BEGIN PRIVATE KEY-----\nAAAAAAAAAAAAAAAAAAAA\n-----END PRIVATE KEY-----"
        " and in between he said hello there "
        "-----BEGIN PRIVATE KEY-----\nBBBBBBBBBBBBBBBBBBBB\n-----END PRIVATE KEY-----"
    )
    assert REDACTOR.text(two) == "[secret] and in between he said hello there [secret]"


def test_many_private_key_headers_with_no_end_are_not_quadratic() -> None:
    text = "-----BEGIN RSA PRIVATE KEY-----\n" * 8000
    started = time.monotonic()
    assert REDACTOR.text(text).count("[secret]") == 8000  # each header is cut alone
    assert time.monotonic() - started < 2  # a loose bound: it takes a few tenths of a second


def test_a_short_decoded_form_of_a_secret_does_not_destroy_other_text() -> None:
    text = "AT&T p&q map&reduce"
    assert Redactor(secrets=["p&amp;"]).text(text) == text


@pytest.mark.parametrize(
    "text",
    ["call <b (11) 91234-5678> now", 'see <a title="(11) 91234-5678">x</a> ok'],
)
def test_a_phone_in_a_tag_or_an_attribute_is_found(text: str) -> None:
    redacted = REDACTOR.text(text)
    assert "91234" not in redacted and "[phone]" in redacted


# -- the round-4 regressions -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/andrezorzo/MyRepo/blob/main/src/v2",
        "https://docs.python.org/3/library/asyncio/eventloop/AbstractEventLoop",
        "https://github.com/facebook/react/tree/main/packages/ReactDOM/v18",
        "https://aws.amazon.com/blogs/machine-learning/2024/11/27/AmazonBedrockKnowledgeBases",
        "https://openai.com/index/IntroducingGPT4/v1/notes/2024/11/some-longer-title",
    ],
)
def test_camel_case_path_parts_are_words(url: str) -> None:
    assert REDACTOR.text(url) == url
    assert REDACTOR.text(f"see {url} for more") == f"see {url} for more"


def test_letters_in_random_case_are_not_camel_case() -> None:
    from rag_core.services.redact import _is_a_word  # the rule itself, since a key is told by it

    assert not _is_a_word("wJalrXUtnFEMI") and not _is_a_word("bPxRfiCYEXAMPLEKEY")
    assert _is_a_word("MyRepo") and _is_a_word("ReactDOM") and _is_a_word("IntroducingGPT4")


def test_a_private_key_in_a_json_string_or_a_quote_block_is_cut_whole() -> None:
    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\\nabcd+/EFGH=="
    json_text = f'{{"private_key": "-----BEGIN PRIVATE KEY-----\\n{body}\\n-----END PRIVATE KEY-----\\n"}}'
    redacted = REDACTOR.text(json_text)
    assert "MIIEvQ" not in redacted and "abcd" not in redacted and "END PRIVATE" not in redacted
    quoted = (
        "> -----BEGIN PRIVATE KEY-----\n> MIIEvQIBADANBg\n> abcd+/EFGH==\n> -----END PRIVATE KEY-----\nthen"
    )
    assert REDACTOR.text(quoted) == "> [secret]\nthen"


@pytest.mark.parametrize(
    "text",
    [
        "ranking: 202 > 555 > 0173 votes, the rest of the answer here",
        "sorted: 415 < 555 < 0132 ok",
    ],
)
def test_angle_brackets_between_numbers_are_not_a_phone(text: str) -> None:
    assert REDACTOR.text(text) == text


def test_a_number_after_a_tag_is_cut_where_it_is() -> None:
    assert REDACTOR.text("<b>Phone:</b> 11 > 98765-4321") == "<b>Phone:</b> [phone]"


def test_a_single_case_letter_token_in_a_path_is_still_a_key() -> None:
    redacted = REDACTOR.text("token users/12345/ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGH")
    assert "ABCDEFGH" not in redacted and "[secret]" in redacted


# -- the last round ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "Ab3dEfGh/IjKlMn/OpQrSt9u/VwXyZa/bCdEfG",
        "a1B2c3D4/EfGhIj/kL5mN6oP/QrStUv/wX7yZ8aB",
    ],
)
def test_a_base64_key_with_short_random_case_parts_is_redacted(key: str) -> None:
    assert REDACTOR.text(f"key {key} end") == "key [secret] end"


@pytest.mark.parametrize(
    "text",
    ["(11)>98765-4321", "11<98765-4321", "(11) 9<8765-4321", "+55<11<98765<4321", "11 > 98765-4321"],
)
def test_a_phone_split_by_an_angle_bracket_is_found_without_a_tag(text: str) -> None:
    assert REDACTOR.text(f"call {text} now") == "call [phone] now"


def test_a_ranking_with_brackets_is_not_a_phone() -> None:
    for text in ("ranking: 202 > 555 > 0173 votes, the rest", "sorted: 415 < 555 < 0132 ok"):
        assert REDACTOR.text(text) == text


def test_prose_between_the_pem_markers_is_not_swallowed() -> None:
    text = 'Replace "-----BEGIN PRIVATE KEY-----" with "-----END PRIVATE KEY-----" in the file'
    assert REDACTOR.text(text) == 'Replace "[secret]" with "-----END PRIVATE KEY-----" in the file'
