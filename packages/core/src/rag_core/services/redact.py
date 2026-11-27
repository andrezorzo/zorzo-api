"""The trace redactor (LLD "3. Security", trace redaction): what the stored trace and LangSmith get has passed
through here first.

It removes hidden characters, replaces personal data (the detectors of `rag_core.pii`, entries of the
domain's `pii_allowlist` pass), the exact value of every secret the app holds, and anything shaped like a
key. It fails closed: a long token that isn't a key is replaced too.
"""

import re
from collections.abc import Iterable
from typing import Any

from rag_core import pii
from rag_core.hidden import strip_hidden

SECRET_MARK = "[secret]"
MIN_SECRET_CHARS = 4  # a shorter value would replace its characters everywhere
MIN_TOKEN_CHARS = 32  # an unbroken run this long, mixing letters and digits, is taken for a key
KEPT_CONTROLS = "\n\t"  # the hidden characters (`rag_core.hidden`) ordinary text keeps
# Key shapes: a known provider prefix, a JWT, a private key header.
KEY_SHAPE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|gsk_[A-Za-z0-9]{8,}|nvapi-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|xox[abprs]-[A-Za-z0-9-]{8,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{12,}|lsv2_[A-Za-z0-9_]{8,}"
    r"|eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,})"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
TOKEN = re.compile(rf"(?<![A-Za-z0-9_+=-])[A-Za-z0-9_+=-]{{{MIN_TOKEN_CHARS},}}(?![A-Za-z0-9_+=-])")


class Redactor:
    """`allowlist`: the domain's `pii_allowlist`. `secrets`: every secret value the app holds (the settings'
    `secret_values()`, plus whatever a later setting adds, such as the canary)."""

    def __init__(self, allowlist: Iterable[str] = (), secrets: Iterable[str] = ()) -> None:
        self._allowed = pii.allowed(allowlist)  # prepared once, not per string
        # Longest first, so a secret that holds another is replaced whole.
        values = sorted({v for v in secrets if len(v.strip()) >= MIN_SECRET_CHARS}, key=len, reverse=True)
        self._secrets = re.compile("|".join(re.escape(v) for v in values)) if values else None

    def text(self, value: str) -> str:
        """The text with nothing hidden, no personal data and no secret."""
        value = strip_hidden(value, keep=KEPT_CONTROLS)
        if self._secrets is not None:
            value = self._secrets.sub(SECRET_MARK, value)
        value = KEY_SHAPE.sub(SECRET_MARK, value)
        value = TOKEN.sub(lambda m: SECRET_MARK if _mixes_letters_and_digits(m.group()) else m.group(), value)
        return pii.scrub(value, self._allowed)

    def walk(self, value: Any) -> Any:
        """A copy of a JSON-like value with every string redacted; other scalars pass unchanged."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.walk(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.walk(v) for v in value]
        return value


def _mixes_letters_and_digits(token: str) -> bool:
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)
