"""Personal-data detectors (LLD "6.1 Corpus version", "3. Security", trace redaction): emails, phones,
CPFs and card numbers, shared by the ingest gate (which refuses a document that has any) and the trace
redactor (which replaces each match).
"""

import functools
import html
import itertools
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass

import phonenumbers

# Anything shaped like `local@domain` with a letter TLD counts as an email, except two exact shapes that are
# not addresses: a retina asset name (`logo@2x.png`, `logo@2x-dark.png`) and a semantic version
# (`pkg@1.0.0-beta`). It fails closed: `icon@2x.dark.png` is flagged too, because it can't be told from an
# address inside a file name, and a false hit only means rewording one line.
# Starts only at the beginning of a run and never backs off (possessive), so a long token without `@` is
# read once instead of once per character.
EMAIL_CHAIN = re.compile(r"(?<![\w.+-])[\w.+-]*+(?:@[\w.-]++)+")
LETTER_TLD = re.compile(r"\.[A-Za-z]{2,}(?![A-Za-z0-9])")
RETINA_ASSET = re.compile(
    r"\d+(?:\.\d+)?x(?:-[\w-]+)?\.(?:png|jpe?g|gif|webp|svg|avif|ico|bmp|tiff?|heic)", re.IGNORECASE
)
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")

# Phones come from Google's libphonenumber, which knows the real numbering plans of Brazil and the US, so a
# year range or a port number isn't a phone. A line wrapped once inside a number is joined first.
PHONE_REGIONS = ("BR", "US")

# Text preparation for the phone scan, in this order, each step one linear pass (see _prepare_for_phones):
# Real tags only (`<tel:…>` and `<+55 …>` stay), on one line and bounded, so a stray `<word` never swallows
# the paragraphs after it.
HTML_TAG = re.compile(r"</?[A-Za-z][\w-]*(?:\s[^<>\n]{0,200})?>")
MARKUP_CHAR = re.compile(r"[*_`\\\u200b-\u200d\u2060\ufeff]")  # emphasis, code, escapes, zero-width
# A wrapped line, not a paragraph break. It starts only where a run of spaces starts and never backs off, so a
# long run of spaces is read once instead of once per space.
ONE_LINE_BREAK = re.compile(r"(?<![^\S\n])[^\S\n]*+\n(?![^\S\n]*+\n)[^\S\n]*+")
ODD_SPACE = re.compile(r"[^\S\n]")  # tabs, no-break and thin spaces
SPACE_RUN = re.compile(r" {2,}")
WHITESPACE = re.compile(r"\s+")
ZERO_WIDTH = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
DIGITS = re.compile(r"\d+")
# Digits with separators on one line; a phone may hide inside a longer run (`Ref 2024 11 98765-4321`).
NUMBER_RUN = re.compile(r"[\d(+][\d ().+-]*+")
MAX_RUN_GROUPS = 12
MAX_PHONE_DIGITS = 15  # E.164
MAX_WINDOW_GROUPS = 6  # a phone is rarely written in more groups
CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|\b\d{11}\b")
CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

# What `scrub` puts in the place of a match.
EMAIL_MARK, PHONE_MARK, CPF_MARK, CARD_MARK = "[email]", "[phone]", "[cpf]", "[card]"
# Between the characters of a phone as written: spaces, a wrapped line, emphasis and code marks, a tag.
PHONE_GLUE = r"(?:[\s*_`\\]|<[^<>\n]{0,200}>)*"


def _cpf_valid(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()]
    if len(set(digits)) == 1:
        return False
    for size in (9, 10):
        check = sum(d * w for d, w in zip(digits[:size], range(size + 1, 1, -1), strict=True)) * 10 % 11 % 10
        if check != digits[size]:
            return False
    return True


def _luhn(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()][::-1]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits))
    return total % 10 == 0


def _emails(text: str) -> list[str]:
    """Every address in the text; `a@b@c.com` yields both pairs, so a chained address can't hide."""
    found = []
    for chain in EMAIL_CHAIN.finditer(text):
        parts = chain.group().split("@")
        for local, domain in itertools.pairwise(parts):
            local, domain = local.lstrip("._-+"), domain.rstrip("._-")
            if local and _is_email_domain(domain):
                found.append(f"{local}@{domain}")
    return found


def _is_email_domain(domain: str) -> bool:
    version = SEMVER.fullmatch(domain)
    # A version suffix ending in a letter label (`1.2.3-mail.com`) may be a host, so it isn't exempt.
    if RETINA_ASSET.fullmatch(domain) or (version and not LETTER_TLD.search(domain.partition("-")[2])):
        return False
    return bool(LETTER_TLD.search(domain))


def _prepare_for_phones(text: str) -> str:
    """The text as it reads on the page, one paragraph per line: what the phone matcher should see."""
    text = MARKUP_CHAR.sub(" ", HTML_TAG.sub(" ", text))  # markup becomes a space, never glue
    # Tags are removed before unescaping, so escaped `&lt;…&gt;` stays visible text; markup characters
    # written as entities (`&#42;`) are removed after it.
    text = MARKUP_CHAR.sub(" ", html.unescape(text))
    text = ODD_SPACE.sub(" ", ONE_LINE_BREAK.sub(" ", text))
    return SPACE_RUN.sub(" ", text)


def _windows(run: str) -> list[str]:
    """Consecutive groups of a sentence-sized run of numbers: up to MAX_WINDOW_GROUPS groups and a phone's
    digit count, so a phone written in more groups after another number isn't read (a known limit)."""
    groups = run.split()
    if not 1 < len(groups) <= MAX_RUN_GROUPS:
        return []
    out = []
    for i in range(len(groups)):
        digits = 0
        for j in range(i, len(groups)):
            digits += sum(c.isdigit() for c in groups[j])
            if digits > MAX_PHONE_DIGITS or j - i >= MAX_WINDOW_GROUPS:
                break
            out.append(" ".join(groups[i : j + 1]))
    return out


def _phone_spans(text: str) -> dict[str, set[int]]:
    """The text of every phone-shaped span, with the country codes of its readings in each region's plan.

    The matcher reads the longest run of digits and separators as one candidate and gives up when the run as
    a whole isn't a number, so windows of sentence-sized runs are read too. A long numeric table is read
    only as a whole: there a phone needs its marker ((DD), +CC) to stand out.
    """
    prepared = _prepare_for_phones(text)
    candidates = [prepared]
    for run in NUMBER_RUN.finditer(prepared):
        candidates.extend(_windows(run.group().rstrip(" ().+-")))
    spans: dict[str, set[int]] = {}
    for candidate in candidates:
        for region in PHONE_REGIONS:
            # The default stops after 65,535 candidates, which would leave the end of a long document unread.
            for m in phonenumbers.PhoneNumberMatcher(
                candidate, region, leniency=phonenumbers.Leniency.VALID, max_tries=sys.maxsize
            ):
                spans.setdefault(m.raw_string, set()).add(m.number.country_code or 0)
    return spans


def _digit_groups(text: str) -> tuple[str, ...]:
    return tuple(DIGITS.findall(text))


@dataclass(frozen=True)
class _PhoneShapes:
    international: frozenset[tuple[str, ...]]  # with the country code: valid in any region's reading
    national: frozenset[tuple[int, tuple[str, ...]]]  # (country code, groups): only in that country's reading


@functools.cache
def _allowed_phone_shapes(allowlist: tuple[str, ...]) -> _PhoneShapes:
    """Digit groups of the allowlisted phones: international renderings, and national ones per country.

    A phone entry needs its +country code. The grouping tells the country: `(415) 555-0132` is the US number
    +1 415-555-0132, while `(41) 5555-0132`, the same digits grouped the Brazilian way, stays someone else's.
    """
    international: set[tuple[str, ...]] = set()
    national: set[tuple[int, tuple[str, ...]]] = set()
    fmt = phonenumbers.PhoneNumberFormat
    for entry in allowlist:
        try:
            number = phonenumbers.parse(entry, None)
        except phonenumbers.NumberParseException:
            continue  # national format or not a phone: such entries match as exact strings
        if not phonenumbers.is_valid_number(number):
            continue
        country = number.country_code or 0
        international.add(_digit_groups(phonenumbers.format_number(number, fmt.INTERNATIONAL)))
        international.add(_digit_groups(phonenumbers.format_number(number, fmt.E164)))
        national_text = phonenumbers.format_number(number, fmt.NATIONAL)
        national.add((country, _digit_groups(national_text)))
        national.add((country, ("".join(_digit_groups(national_text)),)))  # written without separators
    return _PhoneShapes(frozenset(international), frozenset(national))


@dataclass(frozen=True)
class Allowed:
    """The domain's `pii_allowlist`, prepared once: exact strings and the shapes of its phones."""

    strings: frozenset[str]
    phones: _PhoneShapes


def allowed(allowlist: Iterable[str] = ()) -> Allowed:
    entries = tuple(allowlist)
    return Allowed(frozenset(_same_spaces(a) for a in entries), _allowed_phone_shapes(entries))


def _unlisted_phones(text: str, ok: Allowed) -> list[str]:
    """The phones the allowlist doesn't cover: neither their exact text nor their shape in one of their
    countries."""
    return [
        raw
        for raw, countries in _phone_spans(text).items()
        if _same_spaces(raw) not in ok.strings
        and _digit_groups(raw) not in ok.phones.international
        and not any((country, _digit_groups(raw)) in ok.phones.national for country in countries)
    ]


def _same_spaces(value: str) -> str:
    return WHITESPACE.sub(" ", value).lower()


def personal_data(text: str, allowlist: Iterable[str] | Allowed = ()) -> list[str]:
    """The PII rules a text breaks; matches equal to an allowlisted string don't count."""
    # CRLF and non-breaking spaces would otherwise dodge the patterns and the allowlist.
    text = text.replace("\r\n", "\n")
    ok = allowlist if isinstance(allowlist, Allowed) else allowed(allowlist)
    # Emails, CPFs and cards are also read as the page renders them: entities decoded, zero-width gone.
    texts = (text, ZERO_WIDTH.sub("", html.unescape(text)))
    rules = []
    if any(_same_spaces(e) not in ok.strings for t in texts for e in _emails(t)):
        rules.append("pii:email")
    if _unlisted_phones(text, ok):
        rules.append("pii:phone")
    if any(_cpf_valid(m.group()) for t in texts for m in CPF.finditer(t)):
        rules.append("pii:cpf")
    if any(_luhn(m.group()) for t in texts for m in CARD.finditer(t)):
        rules.append("pii:card")
    return rules


def spans(text: str, ok: Allowed) -> list[tuple[int, int, str]]:
    """Where the text holds an email, phone, CPF or card number the rules of `personal_data` find, as
    `(start, end, placeholder)`; allowlisted ones are left out. Nothing is rewritten: the caller merges these
    with other detectors' spans (`rag_core.services.redact`).

    The text should already be as a page renders it (entities decoded, hidden characters gone).
    """
    found = [
        (m.start(), m.end(), EMAIL_MARK)
        for m in EMAIL_CHAIN.finditer(text)
        if any(_same_spaces(e) not in ok.strings for e in _emails(m.group()))
    ]
    found += [(m.start(), m.end(), CPF_MARK) for m in CPF.finditer(text) if _cpf_valid(m.group())]
    found += [(m.start(), m.end(), CARD_MARK) for m in CARD.finditer(text) if _luhn(m.group())]
    for raw in _unlisted_phones(text, ok):
        located = [(m.start(), m.end(), PHONE_MARK) for m in _phone_in_text(raw).finditer(text)]
        # The matcher read the phone in the text as it prepares it (markup gone, a wrapped line joined). One
        # it can't find again as written is hidden too well to cut out: the whole text goes, failing closed.
        found += located or [(0, len(text), PHONE_MARK)]
    return found


def _phone_in_text(raw: str) -> re.Pattern[str]:
    """The phone as the matcher read it, found again in the text as written: its characters with whitespace,
    markup and tags allowed between them."""
    return re.compile(PHONE_GLUE.join(re.escape(c) for c in raw if not c.isspace()))
