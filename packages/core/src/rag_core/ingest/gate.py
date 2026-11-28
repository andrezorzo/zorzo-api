"""Ingest gate: every document is reviewed, has valid front matter, no secret and no personal data.

The whole source is checked before anything is embedded or written; a problem names the file and the rule,
never the match (LLD "6.1 Corpus version").
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from rag_core.domain import DomainConfig
from rag_core.ingest.frontmatter import DocumentMeta, FrontMatterError, parse
from rag_core.pii import personal_data
from rag_core.ports import Finding, SecretScanner

__all__ = ["Document", "check", "personal_data"]  # the detectors live in `rag_core.pii`


@dataclass(frozen=True)
class Document:
    meta: DocumentMeta
    body: str
    hash: str  # SHA-256 of the file, stored with the document


def _undeclared(meta: DocumentMeta, domain: DomainConfig) -> list[str]:
    """Front matter values the domain doesn't declare, named by field with the domain's own list."""
    declared = (
        ("kind", meta.kind, "kinds", domain.document_kinds),
        ("lang", meta.lang, "languages", domain.languages),
    )
    return [
        f"invalid front matter ({field}); the domain's {plural}: {', '.join(values)}"
        for field, value, plural, values in declared
        if value not in values
    ]


async def check(
    source: Path, scanner: SecretScanner, domain: DomainConfig
) -> tuple[list[Document], list[Finding]]:
    documents: list[Document] = []
    problems: list[Finding] = []
    seen: set[str] = set()
    for path in sorted(source.glob("*.md")):
        if path.is_symlink():
            # gitleaks doesn't follow links, so a linked file would be ingested without its secret scan.
            problems.append(Finding(path.name, "symlink"))
            continue
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
        problems.extend(Finding(path.name, rule) for rule in _undeclared(meta, domain))
        if not meta.reviewed_by_owner:
            problems.append(Finding(path.name, "not reviewed_by_owner"))
        if meta.doc_key in seen:
            problems.append(Finding(path.name, "duplicate doc_key"))
        seen.add(meta.doc_key)
        # The whole text: front matter values (the title) are published and indexed too.
        problems.extend(Finding(path.name, rule) for rule in personal_data(text, domain.pii_allowlist))
        documents.append(Document(meta, body, hashlib.sha256(raw).hexdigest()))
    problems.extend(await scanner.scan(source))
    return documents, problems
