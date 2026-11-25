"""The question as the rest of the pipeline sees it (LLD "Security", input)."""

import unicodedata

from rag_core.errors import ApiError, ErrorCode
from rag_core.limits import Limits

# Removed: control characters (Cc) and format characters (Cf). Cf holds the zero-width and bidi controls,
# which hide text from a reader but not from a model. Whitespace (tab and line breaks too, though they are
# Cc) separates words, so it becomes a space instead.
_REMOVED = {"Cc", "Cf"}


def normalize(question: str, limits: Limits) -> str:
    """NFKC, Cc and Cf removed, whitespace collapsed, trimmed; 1 to `max_question_chars` code points.

    Anything else is `invalid_input` before the stream opens. The cap counts the normalized text, since that
    is what the pipeline reads.
    """
    text = "".join(
        " " if c.isspace() else c
        for c in unicodedata.normalize("NFKC", question)
        if c.isspace() or unicodedata.category(c) not in _REMOVED
    )
    text = " ".join(text.split())
    if not text or len(text) > limits.max_question_chars:
        raise ApiError(ErrorCode.INVALID_INPUT)
    return text
