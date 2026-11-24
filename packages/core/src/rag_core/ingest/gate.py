"""Ingest gate: every document is reviewed, has valid front matter, no secret and no personal data.

The whole source is checked before anything is embedded or written; a problem names the file and the rule,
never the match (LLD "6.1 Corpus version").
"""

import hashlib
import itertools
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from rag_core.ingest.frontmatter import DocumentMeta, FrontMatterError, parse
from rag_core.ports import Finding, SecretScanner

# Anything shaped like `local@domain` with a letter TLD counts as an email, except two exact shapes that are
# not addresses: a retina asset name (`logo@2x.png`, `logo@2x-dark.png`) and a semantic version
# (`pkg@1.0.0-beta`). It fails closed: `icon@2x.dark.png` is flagged too, because it can't be told from an
# address inside a file name, and a false hit only means rewording one line.
EMAIL_CHAIN = re.compile(r"[\w.+-]*(?:@[\w.+-]+)+")
LETTER_TLD = re.compile(r"\.[A-Za-z]{2,}(?![A-Za-z0-9])")
RETINA_ASSET = re.compile(
    r"\d+(?:\.\d+)?x(?:-[\w-]+)?\.(?:png|jpe?g|gif|webp|svg|avif|ico|bmp|tiff?|heic)", re.IGNORECASE
)
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")

# Phone formats with a marker that a year range or an id doesn't have: a +country code or parentheses around
# the area code (a wrapped line may split them), or the Brazilian mobile shape (area code, 9 and eight
# digits) on one line. `[^\S\n]` is any space but a line break, non-breaking spaces included.
_SP = r"[^\S\n]"
_WRAP = r"(?:[^\S\n]|\n(?!\n))"
PHONE = re.compile(
    rf"\+\d{{1,3}}(?:{_WRAP}|[.-])?\(?\d{{2,3}}\)?(?:{_WRAP}|[.-])?\d{{3,5}}(?:{_SP}|[.-])?\d{{4}}\b"
    rf"|\(\d{{2,3}}\){_WRAP}?\d{{3,5}}(?:{_SP}|[.-])?\d{{4}}\b"
    rf"|\b\d{{2}}(?:{_SP}|-)9{_SP}?\d{{4}}(?:{_SP}|-)?\d{{4}}\b"
)
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
    if RETINA_ASSET.fullmatch(domain) or SEMVER.fullmatch(domain):
        return False
    return bool(LETTER_TLD.search(domain))


def personal_data(text: str, allowlist: Iterable[str] = ()) -> list[str]:
    """The PII rules a text breaks; matches equal to an allowlisted string don't count."""
    allowed = {a.lower() for a in allowlist}
    rules = []
    if any(e.lower() not in allowed for e in _emails(text)):
        rules.append("pii:email")
    if any(m.group().lower() not in allowed for m in PHONE.finditer(text)):
        rules.append("pii:phone")
    if any(_cpf_valid(m.group()) for m in CPF.finditer(text)):
        rules.append("pii:cpf")
    if any(_luhn(m.group()) for m in CARD.finditer(text)):
        rules.append("pii:card")
    return rules


async def check(
    source: Path, scanner: SecretScanner, allowlist: Iterable[str]
) -> tuple[list[Document], list[Finding]]:
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
