"""The answer contract and its verification (LLD "6.4 Answer contract and verification").

The answer cites passages by number (1 to the number of passages in the prompt); the service maps them to
chunk ids. A claim citing no passage, or one outside the request's passages, is removed without a judge
vote; the judge labels the rest against their cited passages only.
"""

import json
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.utils.json import parse_partial_json
from pydantic import BaseModel, ConfigDict

from rag_core.ports import SearchHit

Verdict = Literal["supported", "partial", "unsupported"]
CheckedVerdict = Literal[Verdict, "gap"]  # `gap`: the sentence naming what the passages don't cover

_STRICT = ConfigDict(extra="forbid", frozen=True)
# Where the answer's JSON starts: after a Markdown fence when there is one, otherwise at a brace opening a
# key, so a brace in prose before it doesn't count.
_FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n?")
_JSON_START = re.compile(r'\{\s*"')
_CLOSING_FENCE = "```"
# Our prompt elements, however spaced or written (the full-width less-than sign too); model or visitor text
# inside one must not open or close any of them.
FULLWIDTH_LESS_THAN = "\uff1c"
_DELIMITER = re.compile(rf"[<{FULLWIDTH_LESS_THAN}]\s*(/?)\s*(claim|checked_claims|passage)\b", re.IGNORECASE)


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


def _json_starts(text: str) -> list[int]:
    """Where the answer's JSON may start, most likely first: the first brace opening a key, and the first one
    after a Markdown fence; the fence's goes first when the fence comes before any such brace."""
    first = _JSON_START.search(text)
    fence = _FENCE.search(text)
    after_fence = _JSON_START.search(text, fence.end()) if fence is not None else None
    starts = [m.start() for m in (first, after_fence) if m is not None]
    if fence is not None and (first is None or fence.start() < first.start()):
        starts.reverse()
    return list(dict.fromkeys(starts))


def _candidates(text: str, parser: Callable[[str], Any]) -> Iterator[Any]:
    """`text` parsed from each possible start, with or without a closing fence after the JSON."""
    for start in _json_starts(text):
        part = text[start:]
        cut = part.rfind(_CLOSING_FENCE)
        for piece in (part, part[:cut]) if cut > 0 else (part,):
            try:
                yield parser(piece)
            except ValueError:
                continue


def read_answer(text: str) -> AnswerContract | None:
    """The answer from its whole text (JSON mode, fenced JSON, or JSON after a sentence); None when it
    doesn't validate. It tries the starts `draft_text` tries, so the draft and the answer agree."""
    for parsed in _candidates(text, json.loads):
        try:
            return AnswerContract.model_validate(parsed)
        except ValueError:  # JSON that isn't the contract
            continue
    return None


def draft_text(partial: str) -> str:
    """The claims' text so far, read from the answer's JSON as it grows; never JSON or passage numbers."""
    for parsed in _candidates(partial, parse_partial_json):
        claims = parsed.get("claims") if isinstance(parsed, dict) else None
        if isinstance(claims, list):
            texts = [c["text"] for c in claims if isinstance(c, dict) and isinstance(c.get("text"), str)]
            return " ".join(t for t in texts if t)
    return ""


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
    """Text placed inside a prompt element, unable to open or close one of ours (other text is untouched,
    so `List<T>` stays as written)."""
    return _DELIMITER.sub(r"&lt;\1\2", text)


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
