"""Ingest gate: every document is reviewed, has valid front matter, no secret and no personal data.

The whole source is checked before anything is embedded or written; a problem names the file and the rule,
never the match (LLD "6.1 Corpus version").
"""

import functools
import hashlib
import html
import itertools
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import phonenumbers

from rag_core.ingest.frontmatter import DocumentMeta, FrontMatterError, parse
from rag_core.ports import Finding, SecretScanner

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
ONE_LINE_BREAK = re.compile(r"[^\S\n]*\n(?![^\S\n]*\n)[^\S\n]*")
ODD_SPACE = re.compile(r"[^\S\n]")  # no-break, thin and other spaces the matcher doesn't treat as separators
# Markup that disappears when the page renders: emphasis, inline code, backslash escapes, inline HTML tags and
# zero-width characters. A run of it, with the spaces around it, becomes one space.
INVISIBLE_MARKUP = re.compile(r"(?:\s*(?:[*_`\\\u200b-\u200d\u2060\ufeff]|<[^>\n]{1,40}>)+)+\s*")
# Digits with separators: a phone may be the tail of a longer run (`Ref 2024 11 98765-4321`).
NUMBER_RUN = re.compile(r"[\d(+][\d\s().+-]*\d")
MAX_RUN_GROUPS = 12
CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|\b\d{11}\b")
CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


@dataclass(frozen=True)
class Document:
    path: Path
    meta: DocumentMeta
    body: str
    raw: bytes


def file_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


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


def _phone_spans(text: str) -> set[tuple[int, str]]:
    """(country code, text) of every phone-shaped span, read with each region's numbering plan.

    The matcher reads the longest run of digits and separators as one candidate and gives up when the run as
    a whole isn't a number, so every window of up to five space-separated groups of a run is tried too
    (`Ref 2024 11 98765-4321`, `123 11 98765-4321 456`).
    """
    prepared = INVISIBLE_MARKUP.sub(" ", html.unescape(text))
    prepared = ODD_SPACE.sub(" ", ONE_LINE_BREAK.sub(" ", prepared))
    candidates = [prepared]
    for run in NUMBER_RUN.finditer(prepared):
        groups = run.group().split()
        # Sentence-sized runs only: a long numeric table would multiply the work, and the scan of the whole
        # text still reads a phone in it when its marker ((DD), +CC) sets it apart.
        if 1 < len(groups) <= MAX_RUN_GROUPS:
            candidates.extend(
                " ".join(groups[i:j])
                for i in range(len(groups))
                for j in range(i + 1, min(i + 6, len(groups) + 1))
            )
    return {
        (m.number.country_code or 0, m.raw_string)
        for candidate in candidates
        for region in PHONE_REGIONS
        # The default stops after 65,535 candidates, which would leave the end of a long document unread.
        for m in phonenumbers.PhoneNumberMatcher(
            candidate, region, leniency=phonenumbers.Leniency.VALID, max_tries=sys.maxsize
        )
    }


def _digit_groups(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\d+", text))


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


def _unlisted_phone(text: str, allowlist: tuple[str, ...], allowed_strings: set[str]) -> bool:
    shapes = _allowed_phone_shapes(allowlist)
    readings: dict[str, list[int]] = {}
    for country, raw in _phone_spans(text):
        readings.setdefault(raw, []).append(country)

    def covered(raw: str, countries: list[int]) -> bool:
        groups = _digit_groups(raw)
        return (
            _same_spaces(raw) in allowed_strings
            or groups in shapes.international
            or any((country, groups) in shapes.national for country in countries)
        )

    return any(not covered(raw, countries) for raw, countries in readings.items())


def _same_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).lower()


def personal_data(text: str, allowlist: Iterable[str] = ()) -> list[str]:
    """The PII rules a text breaks; matches equal to an allowlisted string don't count."""
    # CRLF and non-breaking spaces would otherwise dodge the patterns and the allowlist.
    text = text.replace("\r\n", "\n")
    allowlist = tuple(allowlist)
    allowed = {_same_spaces(a) for a in allowlist}
    rules = []
    if any(_same_spaces(e) not in allowed for e in _emails(text)):
        rules.append("pii:email")
    if _unlisted_phone(text, allowlist, allowed):
        rules.append("pii:phone")
    if any(_cpf_valid(m.group()) for m in CPF.finditer(text)):
        rules.append("pii:cpf")
    if any(_luhn(m.group()) for m in CARD.finditer(text)):
        rules.append("pii:card")
    return rules


async def check(
    source: Path, scanner: SecretScanner, allowlist: Iterable[str]
) -> tuple[list[Document], list[Finding]]:
    allowlist = tuple(allowlist)  # used once per document
    documents: list[Document] = []
    problems: list[Finding] = []
    seen: set[str] = set()
    for path in sorted(source.glob("*.md")):
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")  # a byte order mark, as Windows editors write, is dropped
        except UnicodeDecodeError:
            problems.append(Finding(path.name, "not UTF-8"))
            continue
        try:
            meta, body = parse(text)
        except FrontMatterError as exc:
            problems.append(Finding(path.name, str(exc)))
            continue
        if not meta.reviewed_by_owner:
            problems.append(Finding(path.name, "not reviewed_by_owner"))
        if meta.doc_key in seen:
            problems.append(Finding(path.name, "duplicate doc_key"))
        seen.add(meta.doc_key)
        # The whole text: front matter values (the title) are published and indexed too.
        problems.extend(Finding(path.name, rule) for rule in personal_data(text, allowlist))
        documents.append(Document(path, meta, body, raw))
    problems.extend(await scanner.scan(source))
    return documents, problems
