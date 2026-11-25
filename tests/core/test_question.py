"""AC2: the question as the pipeline sees it (LLD "Security", input)."""

from pathlib import Path

import pytest
from rag_core.errors import ApiError, ErrorCode
from rag_core.limits import load_limits
from rag_core.services.question import normalize

LIMITS = load_limits(Path(__file__).resolve().parents[2] / "config/limits.yaml")


def test_normalizes_nfkc_and_strips_control_and_format_characters() -> None:
    # A lone surrogate (Cs), full-width letters (NFKC), a NUL and a bell (Cc), a zero-width space and a
    # right-to-left override (Cf), and line breaks between words.
    raw = "\ud800  \uff37\uff48\uff49\uff43\uff48\x00 AWS\u200b services\u202e has\x07 he\r\n\tused?  "
    assert normalize(raw, LIMITS) == "Which AWS services has he used?"


def test_caps_the_length_after_normalization() -> None:
    cap = LIMITS.max_question_chars
    assert normalize("a" * cap, LIMITS) == "a" * cap
    # Format characters don't count: they are removed before the cap.
    assert normalize("a" * cap + "\u200b" * 10, LIMITS) == "a" * cap
    for refused in ("a" * (cap + 1), " \x00\u200b\t\n "):
        with pytest.raises(ApiError) as exc:
            normalize(refused, LIMITS)
        assert exc.value.code is ErrorCode.INVALID_INPUT


def test_nfkc_expansion_counts_against_the_cap() -> None:
    # U+FDFA expands to 18 characters under NFKC: the cap applies to what the pipeline sees.
    cap = LIMITS.max_question_chars
    with pytest.raises(ApiError):
        normalize("a" * (cap - 1) + "ﷺ", LIMITS)
