"""The answer contract and its verification (LLD "6.4 Answer contract and verification").

The answer cites passages by number (1 to the number of passages in the prompt); the service maps them to
chunk ids. A claim citing no passage, or one outside the request's passages, is removed without a judge
vote; the judge labels the rest against their cited passages only.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.utils.json import parse_json_markdown, parse_partial_json
from pydantic import BaseModel, ConfigDict

from rag_core.ports import SearchHit

Verdict = Literal["supported", "partial", "unsupported"]
CheckedVerdict = Literal[Verdict, "gap"]  # `gap`: the sentence naming what the passages don't cover

_STRICT = ConfigDict(extra="forbid", frozen=True)


class AnswerClaim(BaseModel):
    model_config = _STRICT
    text: str
    sources: list[int]  # passage numbers, 1-based


class AnswerContract(BaseModel):
    model_config = _STRICT
    answer_lang: str
    claims: list[AnswerClaim]
    gap: str | None  # one sentence naming the part of the question the passages don't cover
    abstained: bool


class JudgeVerdict(BaseModel):
    model_config = _STRICT
    i: int
    verdict: Verdict
    supported_text: str | None


class Verdicts(BaseModel):
    model_config = _STRICT
    verdicts: list[JudgeVerdict]


@dataclass(frozen=True)
class CitedClaim:
    """A claim with its passages resolved; `passages` is empty when a citation was invalid or missing."""

    text: str
    passages: tuple[SearchHit, ...]


@dataclass(frozen=True)
class CheckedClaim:
    text: str
    sources: tuple[int, ...]  # chunk ids
    verdict: CheckedVerdict


def read_answer(text: str) -> AnswerContract | None:
    """The answer from its whole text (JSON mode or fenced JSON); None when it doesn't validate."""
    try:
        return AnswerContract.model_validate(parse_json_markdown(text))
    except ValueError:  # malformed JSON, or JSON that isn't the contract (ValidationError is a ValueError)
        return None


def draft_text(partial: str) -> str:
    """The claims' text so far, read from the answer's JSON as it grows; never JSON or passage numbers."""
    start = partial.find("{")  # past a Markdown fence or a sentence before the JSON
    if start < 0:
        return ""
    try:
        parsed: Any = parse_partial_json(partial[start:])
    except ValueError:
        return ""
    claims = parsed.get("claims") if isinstance(parsed, dict) else None
    if not isinstance(claims, list):
        return ""
    texts = [c["text"] for c in claims if isinstance(c, dict) and isinstance(c.get("text"), str)]
    return " ".join(t for t in texts if t)


def cite(answer: AnswerContract, passages: Sequence[SearchHit]) -> list[CitedClaim]:
    """Each claim with its cited passages; any number outside 1..n voids all of the claim's citations."""
    cited: list[CitedClaim] = []
    for claim in answer.claims:
        valid = bool(claim.sources) and all(1 <= n <= len(passages) for n in claim.sources)
        hits = tuple(passages[n - 1] for n in dict.fromkeys(claim.sources)) if valid else ()
        cited.append(CitedClaim(claim.text, hits))
    return cited


def judge_input(claims: Sequence[CitedClaim]) -> str:
    """The judge's claims, numbered, each with only its cited passages (claims without any are left out).

    A claim is model text the visitor may have steered, so it can't close its own element."""
    blocks = []
    for i, claim in enumerate(claims):
        if not claim.passages:
            continue
        passages = "\n".join(f"<passage>{hit.content}</passage>" for hit in claim.passages)
        blocks.append(f'<claim i="{i}">{as_data(claim.text)}</claim>\nCited passages:\n{passages}')
    return "\n\n".join(blocks)


def as_data(text: str) -> str:
    """Text placed inside a prompt element, unable to open or close one."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def apply_verdicts(
    claims: Sequence[CitedClaim], verdicts: Verdicts | None, gap: str | None
) -> list[CheckedClaim]:
    """The published claims: a claim without a valid citation or a verdict is `unsupported`; a `partial`
    one keeps only its supported part (without one it is `unsupported`); the gap sentence comes last."""
    by_index = {v.i: v for v in verdicts.verdicts} if verdicts else {}
    checked: list[CheckedClaim] = []
    for i, claim in enumerate(claims):
        sources = tuple(hit.id for hit in claim.passages)
        vote = by_index.get(i) if claim.passages else None
        kept = (vote.supported_text or "").strip() if vote else ""
        if vote and vote.verdict == "supported":
            checked.append(CheckedClaim(claim.text, sources, "supported"))
        elif vote and vote.verdict == "partial" and kept:
            checked.append(CheckedClaim(kept, sources, "partial"))
        else:
            checked.append(CheckedClaim(claim.text, sources, "unsupported"))
    if gap and gap.strip():
        checked.append(CheckedClaim(gap.strip(), (), "gap"))
    return checked


def coherent(claims: Sequence[CheckedClaim]) -> bool:
    """Something is left to publish: at least one claim `supported` or `partial`."""
    return any(c.verdict in ("supported", "partial") for c in claims)
