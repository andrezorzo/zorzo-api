"""The trace redactor (LLD "3. Security", trace redaction): what the stored trace and LangSmith get has passed
through here first.

One pass over one canonical text, with no rule rewriting what another sees:

1. **Canonical text.** Hidden characters (`rag_core.hidden`) are removed, the text is put in NFKC (a fullwidth
   `@` is `@`), and HTML entities are decoded, repeatedly (up to `MAX_ENTITY_LAYERS`), each time with the same
   clean-up. This is what a page would show, and what the redactor returns (the stored trace loses the
   entities).
2. **Spans.** Every detector reports where it matched, on that text, without changing it:
   - the exact value of each secret the app holds (raw and NFKC forms, case-sensitive; a value of
     `LONG_SECRET_CHARS` or more also matches in any case);
   - anything shaped like a key: a known prefix, a JWT, a private key header, a bot token;
   - a long token mixing letters and digits, wherever it sits, URL path segments and query values included
     (a slug of three or more short words, each all letters or all digits, is not one);
   - personal data, from `rag_core.pii`: emails, phones, CPFs, card numbers.
3. **Allowlist.** Where the domain's `pii_allowlist` (NFKC) occurs in the text, the personal-data spans inside
   it are dropped. Secret and key spans never are.
4. **Merge.** Overlapping or adjacent spans become one, with the label of highest priority:
   `[secret]`, `[email]`, `[phone]`, `[cpf]`, `[card]`. A secret that holds a phone number, an email or a key
   is replaced whole, and what is left of it can't be read back.

It fails closed: a long token that isn't a key is replaced too.
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
LONG_SECRET_CHARS = 32  # a secret this long is a random string (a key, a canary): matched in any case
MIN_TOKEN_CHARS = 32  # an unbroken run this long, mixing letters and digits, is taken for a key
MAX_ENTITY_LAYERS = 3  # `&amp;#64;` decodes in two steps
MAX_SLUG_WORD_CHARS = 12  # a word of a slug is shorter than this; a longer part may be a key
MIN_SLUG_WORDS = 3
KEPT_CONTROLS = "\n\t"  # the hidden characters (`rag_core.hidden`) ordinary text keeps
# Priority of the labels when spans overlap.
PRIORITY = {SECRET_MARK: 5, pii.EMAIL_MARK: 4, pii.PHONE_MARK: 3, pii.CPF_MARK: 2, pii.CARD_MARK: 1}
# Key shapes: a known provider prefix, a JWT, a private key header, a Telegram bot token.
KEY_SHAPE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|gsk_[A-Za-z0-9]{8,}|nvapi-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|xox[abprs]-[A-Za-z0-9-]{8,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{12,}|lsv2_[A-Za-z0-9_]{8,}|\d{6,12}:AA[A-Za-z0-9_-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,})"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
# A run of key characters (`=` only as base64 padding, so `?api_key=VALUE` is two runs).
TOKEN = re.compile(rf"(?<![A-Za-z0-9_+-])[A-Za-z0-9_+-]{{{MIN_TOKEN_CHARS},}}={{0,2}}(?![A-Za-z0-9_+=-])")
WORD_SEPARATORS = re.compile(r"[-_]")

Span = tuple[int, int, str]


class Redactor:
    """`allowlist`: the domain's `pii_allowlist`. `secrets`: every secret value the app holds (the settings'
    `secret_values()`, plus whatever a later setting adds, such as the canary)."""

    def __init__(self, allowlist: Iterable[str] = (), secrets: Iterable[str] = ()) -> None:
        self._entries = tuple(_nfkc(a).lower() for a in allowlist)
        self._allowed = pii.allowed(_nfkc(a) for a in allowlist)  # prepared once, not per string
        exact = {form for v in secrets for form in (v, _nfkc(v)) if len(_nfkc(v).strip()) >= MIN_SECRET_CHARS}
        # Longest first, so a secret that holds another is replaced whole.
        self._secrets = _alternation(sorted(exact, key=len, reverse=True), 0)
        self._long_secrets = _alternation(sorted(v for v in exact if len(v) >= LONG_SECRET_CHARS), re.I)

    def text(self, value: str) -> str:
        """The text with nothing hidden, no personal data and no secret."""
        value = _canonical(value)
        spans = [*self._secret_spans(value), *pii.spans(value, self._allowed)]
        return _replace(value, self._outside_the_allowlist(value, spans))

    def walk(self, value: Any) -> Any:
        """A copy of a JSON-like value with every string redacted; other scalars pass unchanged."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.walk(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.walk(v) for v in value]
        return value

    def _secret_spans(self, text: str) -> list[Span]:
        found = [(m.start(), m.end(), SECRET_MARK) for m in KEY_SHAPE.finditer(text)]
        found += [(m.start(), m.end(), SECRET_MARK) for m in TOKEN.finditer(text) if _is_a_key(m.group())]
        for pattern in (self._secrets, self._long_secrets):
            if pattern is not None:
                found += [(m.start(), m.end(), SECRET_MARK) for m in pattern.finditer(text)]
        return found

    def _outside_the_allowlist(self, text: str, spans: list[Span]) -> list[Span]:
        """Without the personal-data spans that lie inside an occurrence of an allowlist entry."""
        if not self._entries:
            return spans
        lowered = text.lower()
        allowed_at = [(at, at + len(entry)) for entry in self._entries for at in _occurrences(lowered, entry)]
        return [
            (start, end, label)
            for start, end, label in spans
            if label == SECRET_MARK or not any(lo <= start and end <= hi for lo, hi in allowed_at)
        ]


def _nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def _canonical(value: str) -> str:
    """The text as a page renders it: nothing hidden, in NFKC, entities decoded."""
    for _ in range(MAX_ENTITY_LAYERS):
        value = _nfkc(strip_hidden(value, keep=KEPT_CONTROLS))
        decoded = html.unescape(value)
        if decoded == value:
            return value
        value = decoded
    return _nfkc(strip_hidden(value, keep=KEPT_CONTROLS))


def _alternation(values: list[str], flags: int) -> re.Pattern[str] | None:
    return re.compile("|".join(re.escape(v) for v in values), flags) if values else None


def _occurrences(text: str, entry: str) -> Iterable[int]:
    at = text.find(entry)
    while entry and at != -1:
        yield at
        at = text.find(entry, at + 1)


def _replace(text: str, spans: list[Span]) -> str:
    """The text with each merged span (overlapping or adjacent ones are one) replaced by its label."""
    out: list[str] = []
    done = 0
    merged: list[Span] = []
    for start, end, label in sorted(spans):
        if merged and start <= merged[-1][1]:
            last_start, last_end, last_label = merged[-1]
            top = max(label, last_label, key=PRIORITY.__getitem__)
            merged[-1] = (last_start, max(last_end, end), top)
        else:
            merged.append((start, end, label))
    for start, end, label in merged:
        out += [text[done:start], label]
        done = end
    out.append(text[done:])
    return "".join(out)


def _is_a_key(token: str) -> bool:
    words = [part for part in WORD_SEPARATORS.split(token.rstrip("=")) if part]
    if len(words) >= MIN_SLUG_WORDS and all(
        (w.isalpha() or w.isdigit()) and len(w) < MAX_SLUG_WORD_CHARS for w in words
    ):
        return False  # a slug
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)
