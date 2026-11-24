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
    documents: int = 0
    chunks: int = 0
    embedded: int = 0
    copied: int = 0


class IngestRejected(Exception):
    def __init__(self, summary: Summary, problems: list[Finding]) -> None:
        self.summary = summary
        self.problems = problems
        super().__init__(f"corpus version {summary.version} rejected: {len(problems)} problem(s)")


class IngestFailed(Exception):
    """A run that stopped for a reason other than the gate; carries the version it had created."""

    def __init__(self, summary: Summary) -> None:
        self.summary = summary
        super().__init__(f"ingest of corpus version {summary.version} failed")


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
    """Write a new candidate version from `source`; nothing is embedded or written unless the gate passes."""
    version = await store.create_candidate(embedder.tag)
    try:
        return await _fill_candidate(version, source, store, embedder, scanner, allowlist, limits)
    except IngestRejected as exc:
        exc.summary = Summary(version, await _reject_quietly(store, version))
        raise
    except Exception as exc:
        raise IngestFailed(Summary(version, await _reject_quietly(store, version))) from exc
    except BaseException:  # cancelled: still never leave an activatable candidate
        await _reject_quietly(store, version)
        raise


async def _reject_quietly(store: CorpusStore, version: int) -> str:
    """Reject the version so it can't be activated; returns the status it is left in.

    A failing reject never hides why the run stopped: it is logged, and the version stays `candidate`.
    """
    try:
        await store.reject(version)
    except Exception:
        log.exception("reject_failed", version=version)
        return "candidate"
    return "rejected"


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


async def activate_and_publish(
    version: int,
    *,
    store: CorpusStore,
    publisher: DataPublisher,
    examples: list[Example],
    require_eval: bool,
) -> str:
    """Activate the version, then publish its brain and corpus data; returns the published folder.

    A failure after the activation raises PublishFailed: the version is live, its data not yet published.
    """
    await activate(version, store=store, require_eval=require_eval)
    try:
        tag = await store.embed_model_tag(version)
        if tag is None:  # unreachable once activated; keeps the type honest
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
