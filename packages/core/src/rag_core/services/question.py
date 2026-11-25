"""The question as the rest of the pipeline sees it (LLD "Security", input)."""

import re
import unicodedata

from rag_core.errors import ApiError, ErrorCode
from rag_core.limits import Limits

# Removed: control characters (Cc) and format characters (Cf). Cf holds the zero-width and bidi controls,
# which hide text from a reader but not from a model. Tab and line breaks become spaces first.
_REMOVED = {"Cc", "Cf"}
_WHITESPACE = re.compile(r"\s+")


def normalize(question: str, limits: Limits) -> str:
    """NFKC, Cc and Cf removed, whitespace collapsed, trimmed; 1 to `max_question_chars` code points.

    Anything else is `invalid_input` before the stream opens. The cap counts the normalized text, since that
    is what the pipeline reads.
    """
    text = unicodedata.normalize("NFKC", question)
    text = _WHITESPACE.sub(" ", text)  # before the removal: \t \n \r are Cc but separate words
    text = "".join(c for c in text if unicodedata.category(c) not in _REMOVED)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text or len(text) > limits.max_question_chars:
        raise ApiError(ErrorCode.INVALID_INPUT)
    return text
