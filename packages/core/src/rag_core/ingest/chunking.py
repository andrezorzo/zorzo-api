"""Split a document by its Markdown headers, then by size (LLD "Values and configuration")."""

import hashlib
from dataclasses import dataclass

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from rag_core.ingest.frontmatter import DocumentMeta

HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]
CHARS_PER_TOKEN = 4  # an estimate, so chunking needs no tokenizer


@dataclass(frozen=True)
class ChunkDraft:
    heading_path: str
    content: str
    tokens: int
    hash: str


def chunk_hash(doc_key: str, heading_path: str, content: str) -> str:
    """Equal text in two documents stays two chunks."""
    return hashlib.sha256("\0".join((doc_key, heading_path, content)).encode()).hexdigest()


def split(meta: DocumentMeta, body: str, chunk_tokens: int, overlap: float) -> list[ChunkDraft]:
    size = chunk_tokens * CHARS_PER_TOKEN
    sized = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=int(size * overlap))
    drafts = []
    for section in MarkdownHeaderTextSplitter(HEADERS).split_text(body):
        headers = [section.metadata[k] for _, k in HEADERS if k in section.metadata]
        path = " > ".join([meta.title, *(h for h in headers if h != meta.title)])
        for piece in sized.split_text(section.page_content):
            content = piece.strip()
            if content:
                drafts.append(
                    ChunkDraft(
                        path,
                        content,
                        max(1, len(content) // CHARS_PER_TOKEN),
                        chunk_hash(meta.doc_key, path, content),
                    )
                )
    return drafts
