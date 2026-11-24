"""The question as the rest of the pipeline sees it (LLD "Security", input).

F01-T04 (gate) extends it with NFKC and control-character removal.
"""

from rag_core.errors import ApiError, ErrorCode
from rag_core.limits import Limits


def normalize(question: str, limits: Limits) -> str:
    """Trimmed, 1 to `max_question_chars` characters; otherwise `invalid_input` before the stream opens."""
    text = question.strip()
    if not text or len(text) > limits.max_question_chars:
        raise ApiError(ErrorCode.INVALID_INPUT)
    return text
