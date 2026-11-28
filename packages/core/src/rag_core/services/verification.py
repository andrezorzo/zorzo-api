"""The answer contract and its verification (LLD "6.4 Answer contract and verification").

The answer cites passages by number (1 to the number of passages in the prompt); the service maps them to
chunk ids. A claim citing no passage, or one outside the request's passages, is removed without a judge
vote; the judge labels the rest against their cited passages only.
"""

import contextlib
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.utils.json import parse_partial_json
from pydantic import BaseModel, ConfigDict

from rag_core.ports import SearchHit
from rag_core.services.guard import links_allowed

Verdict = Literal["supported", "partial", "unsupported"]
CheckedVerdict = Literal[Verdict, "gap"]  # `gap`: the sentence naming what the passages don't cover

_STRICT = ConfigDict(extra="forbid", frozen=True)
# Where the answer's JSON may start: any brace opening a key, tried in order (a fence or a sentence before it,
# or an example object, is passed over until one parses as the answer).
_JSON_START = re.compile(r'\{\s*"')
_DECODER = json.JSONDecoder(strict=False)  # a raw newline inside a string is read, as the draft reads it


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


def _decoded(text: str, allow_unfinished: bool) -> Iterator[tuple[Any, bool]]:
    """The JSON values starting at each possible start, each with whether it is complete; with
    `allow_unfinished`, also what an unfinished one holds so far. Text after a value (a closing fence, a
    sentence) is left out."""
    for match in _JSON_START.finditer(text):
        try:
            yield _DECODER.raw_decode(text, match.start())[0], True
            continue
        except ValueError:
            pass
        if allow_unfinished:
            with contextlib.suppress(ValueError):
                yield parse_partial_json(text[match.start() :]), False


def read_answer(text: str) -> AnswerContract | None:
    """The answer from its whole text (JSON mode, fenced JSON, or JSON after a sentence): the last object
    that validates, the rule `draft_text` follows; None when none does."""
    answers = [_contract(parsed) for parsed, _ in _decoded(text, allow_unfinished=False)]
    return next((a for a in reversed(answers) if a is not None), None)


def _contract(parsed: Any) -> AnswerContract | None:
    try:
        return AnswerContract.model_validate(parsed)
    except ValueError:
        return None


def draft_text(streamed: str) -> str:
    """The claims' text so far, read from the answer's JSON as it grows; never JSON or passage numbers.

    The rule is `read_answer`'s, by position: the object that starts last among those that are the answer
    so far (a whole one that validates, or an unfinished one holding a `claims` list). Once the answer
    streams, a format example or an unreadable object before it is passed over. Known limits, all from a
    model that writes more than the one object it was asked for: an example still streaming shows as the
    draft, and the answer's own draft then can't extend it (tokens are never taken back); with several
    answer objects the last one counts. The checked claims (or the abstention) replace the draft anyway,
    and nothing is published unverified.
    """
    claims: list[Any] | None = None
    for parsed, complete in _decoded(streamed, allow_unfinished=True):
        held = parsed.get("claims") if isinstance(parsed, dict) else None
        if (complete and _contract(parsed) is not None) or (not complete and isinstance(held, list)):
            claims = held
    texts = [c["text"] for c in claims or [] if isinstance(c, dict) and isinstance(c.get("text"), str)]
    return " ".join(t for t in texts if t)


def cite(
    answer: AnswerContract, passages: Sequence[SearchHit], link_hosts: Sequence[str]
) -> list[CitedClaim]:
    """Each claim with its cited passages; any number outside 1..n voids all of the claim's citations, and so
    does a link to a host outside `link_hosts` (the claim is then `unsupported`, with no judge vote)."""
    cited: list[CitedClaim] = []
    for claim in answer.claims:
        valid = (
            bool(claim.sources)
            and all(1 <= n <= len(passages) for n in claim.sources)
            and links_allowed(claim.text, link_hosts)
        )
        hits = tuple(passages[n - 1] for n in dict.fromkeys(claim.sources)) if valid else ()
        cited.append(CitedClaim(claim.text, hits))
    return cited


def judge_input(claims: Sequence[CitedClaim]) -> str:
    """The judge's claims as JSON, numbered, each with only its cited passages (claims without any are left
    out). A claim is model text the visitor may have steered; inside a JSON string it can't pose as
    structure or instructions."""
    rows = [
        {
            "i": i,
            "claim": claim.text,
            "cited_passages": [{"authority": hit.authority, "text": hit.content} for hit in claim.passages],
        }
        for i, claim in enumerate(claims)
        if claim.passages
    ]
    return json.dumps({"claims": rows}, ensure_ascii=False)


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


def enforce_links(
    checked: Sequence[CheckedClaim], cited: Sequence[CitedClaim], link_hosts: Sequence[str]
) -> list[CheckedClaim]:
    """The published texts held to the link allowlist (the citation check saw only the claims as drafted): a
    gap with a bad link is dropped, and a `partial` claim whose kept text has one goes back to its own text,
    `unsupported`."""
    result: list[CheckedClaim] = []
    for i, claim in enumerate(checked):
        if claim.verdict == "partial":
            # The kept text is the judge's own, unless it is the claim as drafted (`cite` checked that one).
            if claim.text == cited[i].text or links_allowed(claim.text, link_hosts):
                result.append(claim)
            else:
                result.append(CheckedClaim(cited[i].text, claim.sources, "unsupported"))
        elif claim.verdict != "gap" or links_allowed(claim.text, link_hosts):
            result.append(claim)  # an `unsupported` claim shows struck through, as text, never as a link
        # a gap with a bad link is dropped
    return result


def coherent(claims: Sequence[CheckedClaim]) -> bool:
    """Something is left to publish: at least one claim `supported` or `partial`."""
    return any(c.verdict in ("supported", "partial") for c in claims)
