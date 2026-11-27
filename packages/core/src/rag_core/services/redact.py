"""The trace redactor (LLD "3. Security", trace redaction): what the stored trace and LangSmith get has passed
through here first.

One pass over one canonical text, with no rule rewriting what another sees:

1. **Canonical text.** Hidden characters (`rag_core.hidden`) are removed, the text is put in NFKC (a fullwidth
   `@` is `@`), and HTML entities are decoded, repeatedly (up to `MAX_ENTITY_LAYERS`), each time with the same
   clean-up. This is what a page would show, and what the redactor returns (the stored trace loses the
   entities). A text that still decodes after that many layers is replaced whole, by `[redacted]`.
2. **Spans.** Every detector reports where it matched, on that text, without changing it:
   - the exact value of each secret the app holds (raw and NFKC forms, case-sensitive; a value of
     `LONG_SECRET_CHARS` or more also matches in any case);
   - anything shaped like a key: a known prefix, a JWT, a private key header, a bot token;
   - a long token mixing letters and digits, wherever it sits, URL path segments and query values included
     (a slug, three or more words of letters, numbers and short mixed words such as `python3`, is not one),
     a base64 run with `/`, and a PEM block from its BEGIN to its END;
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
REDACTED_MARK = "[redacted]"  # the whole text, when it still decodes after `MAX_ENTITY_LAYERS`
MAX_MIXED_WORD_CHARS = 8  # a short mixed word of a slug (`v2`, `python3`); a longer one may be a key
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
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----(?:[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----)?"  # the whole block
)
# A run of key characters, with its `=` padding (`KEY=VALUE` and `?api_key=VALUE` are two runs).
TOKEN = re.compile(rf"(?<![A-Za-z0-9_+-])[A-Za-z0-9_+-]{{{MIN_TOKEN_CHARS},}}=*(?![A-Za-z0-9_+-])")
# A base64 run, which has `/` (outside a URL, whose paths are read by segment).
BASE64 = re.compile(rf"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{{{MIN_TOKEN_CHARS},}}={{0,2}}(?![A-Za-z0-9+/])")
URL = re.compile(r"https?://[^\s<>\"')\]]+")
WORD_SEPARATORS = re.compile(r"[-_]")

Span = tuple[int, int, str]


class Redactor:
    """`allowlist`: the domain's `pii_allowlist`. `secrets`: every secret value the app holds (the settings'
    `secret_values()`, plus whatever a later setting adds, such as the canary)."""

    def __init__(self, allowlist: Iterable[str] = (), secrets: Iterable[str] = ()) -> None:
        self._entries = tuple(re.compile(re.escape(_nfkc(a)), re.IGNORECASE) for a in allowlist if a.strip())
        self._allowed = pii.allowed(_nfkc(a) for a in allowlist)  # prepared once, not per string
        # Each value as written, in NFKC and as a page would show it: any can be what the text holds. Searched
        # one by one, with overlaps allowed, so two secrets that overlap in the text are both cut.
        forms = {f for v in secrets if len(_nfkc(v).strip()) >= MIN_SECRET_CHARS for f in _forms(v) if f}
        self._secrets = [(re.compile(f"(?={re.escape(f)})"), len(f)) for f in sorted(forms)]
        self._long_secrets = [  # a random string (a key, a canary) is found in any case
            (re.compile(f"(?={re.escape(f)})", re.IGNORECASE), len(f))
            for f in sorted(forms)
            if len(f) >= LONG_SECRET_CHARS
        ]

    def text(self, value: str) -> str:
        """The text with nothing hidden, no personal data and no secret."""
        layers = _layers(value)
        if layers is None:  # still decoding after `MAX_ENTITY_LAYERS`: no telling what it holds
            return REDACTED_MARK
        value = layers[-1]
        # A phone can hide in a layer the next one turns into markup (`&lt;b (11) 98765-4321&gt;`).
        spans = [*self._secret_spans(value), *pii.spans(value, self._allowed, layers[:-1])]
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
        urls = [(m.start(), m.end()) for m in URL.finditer(text)]  # their paths are read by segment
        found += [
            (m.start(), m.end(), SECRET_MARK)
            for m in BASE64.finditer(text)
            if _mixes_letters_and_digits(m.group()) and not any(lo <= m.start() < hi for lo, hi in urls)
        ]
        for pattern, length in (*self._secrets, *self._long_secrets):
            found += [(m.start(), m.start() + length, SECRET_MARK) for m in pattern.finditer(text)]
        return found

    def _outside_the_allowlist(self, text: str, spans: list[Span]) -> list[Span]:
        """Without the personal-data spans that lie inside an occurrence of an allowlist entry."""
        if not self._entries:
            return spans
        allowed_at = [(m.start(), m.end()) for entry in self._entries for m in entry.finditer(text)]
        return [
            (start, end, label)
            for start, end, label in spans
            if label == SECRET_MARK or not any(lo <= start and end <= hi for lo, hi in allowed_at)
        ]


def _nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def _clean(value: str) -> str:
    return _nfkc(strip_hidden(value, keep=KEPT_CONTROLS))


def _layers(value: str) -> list[str] | None:
    """The text as written (clean), then after each entity decoding, up to the one a page would show; None
    when it still decodes after `MAX_ENTITY_LAYERS` of them."""
    layers = [_clean(value)]
    for _ in range(MAX_ENTITY_LAYERS):
        decoded = _clean(html.unescape(layers[-1]))
        if decoded == layers[-1]:
            return layers
        layers.append(decoded)
    return layers if _clean(html.unescape(layers[-1])) == layers[-1] else None


def _forms(secret: str) -> set[str]:
    """A secret as it may be written in the text: as is, in NFKC, and as a page would show it."""
    layers = _layers(secret)
    return {secret, _nfkc(secret), *(layers[-1:] if layers else [])}


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
    return _mixes_letters_and_digits(token) and not _is_a_slug(token)


def _is_a_slug(token: str) -> bool:
    """Words and numbers joined by `-` or `_`: a title in a path, a file name. A part is all letters (any
    length), all digits, or a short mixed word (`v2`, `python3`); the mixed ones are the minority, since a key
    cut in groups of letters and digits is all mixed parts."""
    words = [part for part in WORD_SEPARATORS.split(token.rstrip("=")) if part]
    if len(words) < MIN_SLUG_WORDS:
        return False
    mixed = 0
    for word in words:
        if word.isalpha() or word.isdigit():
            continue
        if not word.isalnum() or len(word) > MAX_MIXED_WORD_CHARS:
            return False
        mixed += 1
    return mixed * 2 < len(words)


def _mixes_letters_and_digits(token: str) -> bool:
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)
