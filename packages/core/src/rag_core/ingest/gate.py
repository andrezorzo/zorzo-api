"""Ingest gate: every document is reviewed, has valid front matter, no secret and no personal data.

The whole source is checked before anything is embedded or written; a problem names the file and the rule,
never the match (LLD "6.1 Corpus version").
"""

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from rag_core.ingest.frontmatter import DocumentMeta, FrontMatterError, parse
from rag_core.ports import Finding, SecretScanner

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# International (+CC) or Brazilian ((DD)) formats; a bare run of digits is too often a year range or an id.
PHONE = re.compile(
    r"\+\d{1,3}[\s.-]?\(?\d{2,3}\)?[\s.-]?\d{4,5}[\s.-]?\d{4}\b|\(\d{2,3}\)\s?\d{4,5}-?\d{4}\b"
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


def personal_data(text: str, allowlist: Iterable[str] = ()) -> list[str]:
    """The PII rules a text breaks; matches equal to an allowlisted string don't count."""
    allowed = set(allowlist)
    rules = []
    if any(m.group() not in allowed for m in EMAIL.finditer(text)):
        rules.append("pii:email")
    if any(m.group() not in allowed for m in PHONE.finditer(text)):
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
    for path in sorted(source.glob("*.md")):
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        try:
            meta, body = parse(text)
        except FrontMatterError as exc:
            problems.append(Finding(path.name, str(exc)))
            continue
        if not meta.reviewed_by_owner:
            problems.append(Finding(path.name, "not reviewed_by_owner"))
        problems.extend(Finding(path.name, rule) for rule in personal_data(body, allowlist))
        documents.append(Document(path, meta, body, raw))
    problems.extend(await scanner.scan(source))
    return documents, problems
