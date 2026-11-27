"""Filling the domain's prompt templates (LLD "6.3 Agent run", context window): the messages of a call and
the passages it cites by number."""

from collections.abc import Sequence
from string import Template

from rag_core.ports import SearchHit
from rag_core.services.gateway import estimate_tokens


def messages(template: str, question: str, /, **slots: str) -> list[dict[str, str]]:
    """The filled template as the system message and the question as the user's. Only the template's own
    `$slot`s are replaced; the values are never read as a template."""
    system = Template(template).safe_substitute(slots)
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def passages_text(passages: Sequence[SearchHit]) -> str:
    """The passages, numbered from 1 and marked as data."""
    return "\n".join(
        f'<passage n="{n}" title="{hit.title}">\n{hit.content}\n</passage>'
        for n, hit in enumerate(passages, 1)
    )


def fit_passages(hits: Sequence[SearchHit], max_tokens: int) -> list[SearchHit]:
    """Whole passages in rank order while they fit the prompt's budget; the lowest-ranked go first."""
    kept: list[SearchHit] = []
    used = 0
    for hit in hits:
        used += estimate_tokens(len(hit.content))
        if used > max_tokens:
            break
        kept.append(hit)
    return kept
