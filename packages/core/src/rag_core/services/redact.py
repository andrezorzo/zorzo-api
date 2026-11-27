"""The trace redactor (LLD "3. Security", trace redaction): what the stored trace and LangSmith get has passed
through here first.

It removes hidden characters, replaces personal data (the detectors of `rag_core.pii`, entries of the
domain's `pii_allowlist` pass), the exact value of every secret the app holds, and anything shaped like a
key. It fails closed: a long token that isn't a key is replaced too.
"""

import html
import re
import unicodedata
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
# A URL is passed over whole (its path may hold a long slug); a token is a run of key characters. A canonical
# UUID and a slug (words and numbers joined by `-` or `_`, such as a title in a path) aren't keys.
URL_OR_TOKEN = re.compile(
    rf"(?P<url>https?://[^\s<>\"')\]]+)|(?<![A-Za-z0-9_+=-])(?P<token>[A-Za-z0-9_+=-]{{{MIN_TOKEN_CHARS},}})"
    r"(?![A-Za-z0-9_+=-])"
)
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
WORD_SEPARATORS = re.compile(r"[-_]")
MAX_ENTITY_LAYERS = 3  # `&amp;#64;` decodes in two steps


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
        value = unicodedata.normalize(
            "NFKC", strip_hidden(value, keep=KEPT_CONTROLS)
        )  # a fullwidth at sign is one
        # Keys and long tokens go before personal data, so a digit run inside a key isn't read as a phone and
        # the rest of the key left behind; the exact secrets after it, so a short one inside a longer token
        # leaves nothing of the token. A second pass catches what the personal-data scrub decoded.
        value = pii.scrub(self._keys(value), self._allowed)
        value, decoded = self._secrets_in_every_form(value)
        return (
            pii.scrub(value, self._allowed) if decoded else value
        )  # the decoded text may show personal data

    def walk(self, value: Any) -> Any:
        """A copy of a JSON-like value with every string redacted; other scalars pass unchanged."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.walk(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.walk(v) for v in value]
        return value

    def _keys(self, value: str) -> str:
        """Anything shaped like a key, and tokens that mix letters and digits."""
        value = KEY_SHAPE.sub(SECRET_MARK, value)
        return URL_OR_TOKEN.sub(lambda m: SECRET_MARK if _is_a_key(m) else m.group(), value)

    def _all_secrets(self, value: str) -> str:
        value = self._keys(value)
        return value if self._secrets is None else self._secrets.sub(SECRET_MARK, value)

    def _secrets_in_every_form(self, value: str) -> tuple[str, bool]:
        """The secrets out of the text, and out of the text as a page renders it (entities decoded): a secret
        written as `sk&#45;…` shows as the secret. True when the text returned is the decoded one."""
        value = self._all_secrets(value)
        rendered = value
        for _ in range(MAX_ENTITY_LAYERS):
            decoded = html.unescape(rendered)
            if decoded == rendered:
                break
            rendered = decoded
        if rendered != value:
            redacted = self._all_secrets(rendered)
            if redacted != rendered:  # a secret was hiding behind an entity: keep the decoded, redacted text
                return redacted, True
        return value, False


def _is_a_key(match: re.Match[str]) -> bool:
    token = match.group("token")
    if token is None or UUID.fullmatch(token):
        return False
    words = [part for part in WORD_SEPARATORS.split(token) if part]
    if len(words) > 1 and all(w.isalpha() or w.isdigit() for w in words):
        return False  # a slug
    return _mixes_letters_and_digits(token)


def _mixes_letters_and_digits(token: str) -> bool:
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)
