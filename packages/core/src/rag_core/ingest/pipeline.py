"""Ingest run and activation (LLD "6.1 Corpus version")."""

from dataclasses import dataclass
from pathlib import Path

import structlog

from rag_core.domain import Example
from rag_core.ingest import gate, publish
from rag_core.ingest.chunking import ChunkDraft, split
from rag_core.limits import Limits
from rag_core.ports import ChunkRow, CorpusStore, DataPublisher, DocumentRow, Embedder, Finding, SecretScanner

log = structlog.get_logger()


@dataclass(frozen=True)
class Summary:
    version: int
    status: str
    # Counts of what the run wrote; None when it wrote no chunks (a rejection, an activation alone).
    documents: int | None = None
    chunks: int | None = None
    embedded: int | None = None
    copied: int | None = None


class IngestRejected(Exception):
    def __init__(self, summary: Summary, problems: list[Finding]) -> None:
        self.summary = summary
        self.problems = problems
        super().__init__(f"corpus version {summary.version} rejected: {len(problems)} problem(s)")


class PublishFailed(Exception):
    """The version went live but its brain and corpus data weren't published."""

    def __init__(self, version: int) -> None:
        self.version = version
        super().__init__(f"corpus version {version} is active but its data wasn't published")


class ActivationRefused(Exception):
    pass


async def run_ingest(
    source: Path,
    *,
    store: CorpusStore,
    embedder: Embedder,
    scanner: SecretScanner,
    allowlist: tuple[str, ...],
    limits: Limits,
) -> Summary:
    """Write a new candidate version from `source`; nothing is embedded or written unless the gate passes.

    A run that stops for any reason tries to reject its candidate, so it can't be activated; a failed reject
    is logged (`reject_failed`), and that candidate needs a manual check.
    """
    version = await store.create_candidate(embedder.tag)
    try:
        return await _fill_candidate(version, source, store, embedder, scanner, allowlist, limits)
    except BaseException as exc:  # rejected, failed or cancelled: the original error is what the caller sees
        if isinstance(exc, IngestRejected):
            # Logged before the reject, so the owner learns what to fix even if the reject fails.
            for problem in exc.problems:
                log.error("ingest_rejected", file=problem.file, rule=problem.rule)
        try:
            await store.reject(version)
        except Exception as reject_error:
            log.exception("reject_failed", version=version)
            if isinstance(exc, IngestRejected):
                raise reject_error from exc  # its summary would claim a status the database may not have
        raise


async def _fill_candidate(
    version: int,
    source: Path,
    store: CorpusStore,
    embedder: Embedder,
    scanner: SecretScanner,
    allowlist: tuple[str, ...],
    limits: Limits,
) -> Summary:
    documents, problems = await gate.check(source, scanner, allowlist)
    if not documents and not problems:
        problems = [Finding(str(source), "no documents")]
    if problems:
        raise IngestRejected(Summary(version, "rejected"), problems)

    doc_rows: list[DocumentRow] = []
    drafts: list[tuple[str, ChunkDraft]] = []
    for doc in documents:
        meta = doc.meta
        doc_rows.append(
            DocumentRow(
                doc_key=meta.doc_key,
                title=meta.title,
                kind=meta.kind,
                lang=meta.lang,
                authority=meta.authority,
                source_url=str(meta.source_url) if meta.source_url else None,
                updated=meta.updated.isoformat() if meta.updated else None,
                hash=doc.hash,
            )
        )
        drafts.extend(
            (meta.doc_key, d) for d in split(meta, doc.body, limits.chunk_tokens, limits.chunk_overlap)
        )

    # Identical sections of one document are one chunk.
    drafts = list({d.hash: (key, d) for key, d in drafts}.values())
    reusable = await store.reusable_embeddings([d.hash for _, d in drafts], embedder.tag)
    missing = [d for _, d in drafts if d.hash not in reusable]
    # The heading path goes into the embedded text: it carries the section's context.
    vectors = await embedder.embed([f"{d.heading_path}\n{d.content}" for d in missing]) if missing else []
    fresh = {d.hash: v for d, v in zip(missing, vectors, strict=True)}
    chunk_rows = [
        ChunkRow(key, d.heading_path, d.content, d.tokens, d.hash, reusable.get(d.hash) or fresh[d.hash])
        for key, d in drafts
    ]
    await store.write(version, doc_rows, chunk_rows)
    return Summary(
        version, "candidate", len(doc_rows), len(chunk_rows), len(missing), len(drafts) - len(missing)
    )


async def publish_active(
    version: int, *, store: CorpusStore, publisher: DataPublisher, examples: list[Example]
) -> str:
    """Publish the brain and corpus data of an active version; returns the published folder.

    Any failure raises PublishFailed: the version is live, its data not yet published.
    """
    try:
        tag = await store.embed_model_tag(version)
        if tag is None:
            raise LookupError(f"corpus version {version} does not exist")
        folder, files, manifest = publish.build(version, tag, await store.published_chunks(version), examples)
        await publisher.publish(folder, files, manifest)
    except Exception as exc:
        raise PublishFailed(version) from exc
    return folder


async def activate(version: int, *, store: CorpusStore, require_eval: bool) -> None:
    """With `require_eval` (production), only a version with a passing eval run is activated."""
    if require_eval and not await store.eval_passed(version):
        raise ActivationRefused(f"corpus version {version} has no passing eval run")
    try:
        await store.activate(version)
    except LookupError as exc:
        raise ActivationRefused(str(exc)) from exc
