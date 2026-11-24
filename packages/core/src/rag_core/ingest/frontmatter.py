"""YAML front matter of a corpus document."""

import datetime
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class DocumentMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_key: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    kind: Literal["resume", "narrative", "faq", "repo", "project_doc", "site_doc", "post"]
    lang: Literal["en", "pt"]
    authority: int = Field(ge=1, le=3)
    source_url: HttpUrl | None = None
    updated: datetime.date | None = None
    reviewed_by_owner: bool


class FrontMatterError(ValueError):
    pass


def parse(text: str) -> tuple[DocumentMeta, str]:
    """Split a document into its validated front matter and its Markdown body."""
    if not text.startswith("---\n"):
        raise FrontMatterError("missing front matter")
    try:
        head, body = text[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise FrontMatterError("unterminated front matter") from exc
    try:
        meta = DocumentMeta.model_validate(yaml.safe_load(head))
    except Exception as exc:
        # Only the field names: a value could be what the gate is protecting.
        fields = ", ".join(str(e["loc"][0]) for e in getattr(exc, "errors", lambda: [])()) or "unreadable"
        raise FrontMatterError(f"invalid front matter ({fields})") from None
    return meta, body
