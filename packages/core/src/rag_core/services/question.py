"""The question as the rest of the pipeline sees it (LLD "Security", input)."""

import unicodedata

from rag_core.errors import ApiError, ErrorCode
from rag_core.hidden import HIDDEN
from rag_core.limits import Limits


def normalize(question: str, limits: Limits) -> str:
    """NFKC, Cc, Cf and Cs removed, whitespace collapsed, trimmed; 1 to `max_question_chars` code points.

    Anything else is `invalid_input` before the stream opens. The cap counts the normalized text, since that
    is what the pipeline reads.
    """
    # Hidden characters go; whitespace (tab and line breaks too, though they are Cc) separates words, so it
    # becomes a space instead.
    text = HIDDEN.sub(lambda m: " " if m.group().isspace() else "", unicodedata.normalize("NFKC", question))
    text = " ".join(text.split())
    if not text or len(text) > limits.max_question_chars:
        raise ApiError(ErrorCode.INVALID_INPUT)
    return text
