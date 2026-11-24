"""YAML front matter of a corpus document."""

import datetime
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError


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
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise FrontMatterError("missing front matter")
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        if not text.rstrip("\n").endswith("\n---"):
            raise FrontMatterError("unterminated front matter")
        head, body = text[4:].rstrip("\n")[: -len("\n---")], ""
    try:
        raw = yaml.safe_load(head)
    except Exception:  # bad YAML, an impossible date (PyYAML builds dates), nesting too deep
        raise FrontMatterError("invalid front matter (unreadable YAML)") from None
    if not isinstance(raw, dict):
        raise FrontMatterError("invalid front matter (not a mapping)")
    try:
        meta = DocumentMeta.model_validate(raw)
    except ValidationError as exc:
        # Only the field names: a value could be what the gate is protecting.
        # An unknown key's name could itself be personal data, so it is reported as "unknown field".
        fields = (
            ", ".join(
                sorted(
                    {
                        "unknown field" if e["type"] == "extra_forbidden" else str(e["loc"][0])
                        for e in exc.errors()
                        if e["loc"]
                    }
                )
            )
            or "values"
        )
        raise FrontMatterError(f"invalid front matter ({fields})") from None
    return meta, body
