"""The answer cache's rules (ADR 0014, LLD "6.9 Answer cache"): what is looked up, what a hit replays and
what is stored. The statements are the adapter's.

The cache never fails a question: a lookup that raises or runs out of time is a miss, and a write that
fails is dropped; each is logged at `warning` with the error type only, never the question.
"""

import asyncio
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError

from rag_core.background import BackgroundTasks
from rag_core.events import Claim, ContactOption, Done, Usage
from rag_core.limits import Limits
from rag_core.pii import allowed, personal_data
from rag_core.ports import AnswerCache, CacheEntry, Embedder, TraceSink

log = structlog.get_logger()


@dataclass(frozen=True)
class Lookup:
    """What a lookup found: the stored claims on a hit, or the question's vector (kept for the row a miss
    may store) on a miss. Both are None when the embedding itself failed."""

    claims: list[Claim] | None = None
    vector: list[float] | None = None


class AnswerCacheService:
    def __init__(
        self,
        store: AnswerCache,
        embedder: Embedder,
        limits: Limits,
        model_config_version: str,
        languages: Collection[str],
        pii_allowlist: Sequence[str] = (),
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.limits = limits
        self.model_config_version = model_config_version
        self.languages = frozenset(languages)
        self._allowlist = allowed(pii_allowlist)
        self._writes = BackgroundTasks()

    def skips(self, question: str, lang: str | None) -> bool:
        """Whether the question is neither looked up nor stored: a language the domain doesn't declare (its
        answer carries the note naming the site's languages), or personal data in the question."""
        return lang not in self.languages or bool(personal_data(question, self._allowlist))

    async def lookup(
        self,
        question: str,
        lang: str,
        corpus_version: int,
        deadline_at: float,
        publishable: Callable[[Sequence[Claim]], bool],
    ) -> Lookup:
        """The nearest stored answer to `question`; a miss on any failure or when `deadline_at` passes. A
        stored answer that no longer reads as claims, or that `publishable` refuses (the checks every
        published claim passes), is a miss and is deleted, so it can't shadow the row stored after it."""
        vector: list[float] | None = None
        claims: list[Claim] | None = None
        try:
            async with asyncio.timeout_at(deadline_at):
                [vector] = await self.embedder.embed([question])
                found = await self.store.lookup(
                    vector, lang, corpus_version, self.model_config_version, self.limits.cache_similarity
                )
                if found is not None:
                    claims = _read(found.claims)
                    if claims is None or not publishable(claims):
                        claims = None
                        await self._discard(found.id)
        except Exception as exc:  # a cache that fails is a miss, never an error for the visitor
            log.warning("cache_lookup_failed", error=type(exc).__name__)
            return Lookup(vector=vector)
        return Lookup(claims=claims, vector=vector)

    async def _discard(self, entry_id: int) -> None:
        try:
            await self.store.delete(entry_id)
        except Exception as exc:  # best effort: the row stays until it expires
            log.warning("cache_delete_failed", error=type(exc).__name__)

    def remember(
        self, question: str, lang: str, corpus_version: int, vector: list[float], claims: Sequence[Claim]
    ) -> None:
        """Stores a verified answer in the background, so the write never holds the stream."""
        entry = CacheEntry(
            question=question,
            lang=lang,
            corpus_version=corpus_version,
            model_config_version=self.model_config_version,
            claims=[claim.model_dump(mode="json") for claim in claims],
            vector=vector,
        )
        self._writes.spawn(self._write(entry))

    async def _write(self, entry: CacheEntry) -> None:
        try:
            await self.store.store(entry, self.limits.cache_ttl)
        except Exception as exc:
            log.warning("cache_write_failed", error=type(exc).__name__)

    async def drain(self, timeout: float | None = None) -> None:
        """Wait for the pending writes, at shutdown and in tests."""
        await self._writes.drain(timeout)


def end_with_hit(
    sink: TraceSink,
    claims: Sequence[Claim],
    usage: Usage,
    trace_id: UUID,
    contact: Sequence[ContactOption],
) -> None:
    """The end of a cache hit, for every path that serves one: the stored claims as they were first sent
    (the lookup already held them to the publish checks), `usage`, then `done` as a cached answer."""
    for claim in claims:
        sink.record(claim)
    sink.record(usage)
    sink.record(
        Done(trace_id=trace_id, outcome="answered", cached=True, suggestion=None, contact=list(contact))
    )


def _read(stored: list[dict[str, Any]]) -> list[Claim] | None:
    """The stored claims validated as the stream's `Claim` events, in order; None (a miss) when there are none
    or one no longer reads as a claim (a later change to the event)."""
    try:
        claims = [Claim.model_validate(c) for c in stored]
    except ValidationError:
        return None
    return claims or None
