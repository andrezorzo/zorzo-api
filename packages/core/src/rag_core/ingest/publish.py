"""Brain and corpus data for the front (LLD "Published data")."""

import datetime
import hashlib
import json
from typing import Any

import numpy as np

from rag_core.domain import Example
from rag_core.ports import PublishedChunk


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def positions(embeddings: list[list[float]]) -> list[list[float]]:
    """A 3D PCA of the embeddings, each axis scaled to [-1, 1]; the front maps it into the brain."""
    if not embeddings:
        return []
    x = np.asarray(embeddings, dtype=np.float64)
    x -= x.mean(axis=0)
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    p = x @ vt[:3].T
    if p.shape[1] < 3:
        p = np.pad(p, ((0, 0), (0, 3 - p.shape[1])))
    scale = np.abs(p).max(axis=0)
    p = np.divide(p, scale, out=np.zeros_like(p), where=scale > 0)
    return [[round(float(v), 4) for v in row] for row in p]


def build(
    version: int, embed_model_tag: str, chunks: list[PublishedChunk], examples: list[Example]
) -> tuple[str, dict[str, bytes], bytes]:
    """The content-addressed folder, its files and the manifest that points at it."""
    corpus = {
        "corpus_version": version,
        "chunks": [
            {
                "id": c.id,
                "doc_key": c.doc_key,
                "title": c.title,
                "kind": c.kind,
                "lang": c.lang,
                "heading": c.heading,
                "text": c.text,
                "source_url": c.source_url,
                "p": p,
            }
            for c, p in zip(chunks, positions([c.embedding for c in chunks]), strict=True)
        ],
    }
    files = {"corpus.json": _json(corpus), "examples.json": _json([e.model_dump() for e in examples])}
    digest = hashlib.sha256(files["corpus.json"] + b"\0" + files["examples.json"]).hexdigest()
    folder = f"data/{digest}/"
    manifest = {
        "corpus_version": version,
        "embed_model_tag": embed_model_tag,
        "path": folder,
        "published_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }
    return folder, files, _json(manifest)
