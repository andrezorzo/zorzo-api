"""Ports: interfaces for what lives outside the core (LLD "Components and dependencies")."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from rag_core.events import StreamEvent

# Every candidate embedding model has 1024 dimensions (LLD "Open questions"); the dashboard's models
# declare the same size for the `vector` columns.
EMBEDDING_DIM = 1024


class HealthProbe(Protocol):
    """What the health check needs from the database."""

    async def ping(self) -> None:
        """Raise when the database can't be reached."""


class BotCheck(Protocol):
    """Verifies the visitor's bot-check token (LLD "Security", bot check)."""

    async def verify(self, token: str) -> bool:
        """True only when the provider accepts the token; a provider error or timeout is False."""

    async def close(self) -> None:
        """Release the connection to the provider, at shutdown."""


@dataclass(frozen=True)
class Bucket:
    """One token bucket of a question (LLD "6.7 Rate limit").

    `refill_per_s` is 0 for a bucket that doesn't refill; it then ends at `resets_at` (a new key takes over).
    """

    key: str
    kind: (
        str  # what a log line may say about the bucket: `ip_burst`, `ip_day`, `session_burst`, `session_day`
    )
    capacity: int
    refill_per_s: float
    resets_at: datetime | None = None


class StoreUnavailable(Exception):
    """A store refused or failed the request (database down, a lock wait or long statement it ended)."""


class RateLimiter(Protocol):
    async def take(self, buckets: list[Bucket], now: datetime) -> list[tuple[Bucket, float]] | None:
        """Take one token from every bucket, or from none.

        None when every bucket had one; otherwise the empty buckets, each with the seconds until it holds one.
        A store that fails raises `StoreUnavailable`, which the gate answers as `unavailable`. One that stops
        answering may block: the caller bounds the call.
        """


class Embedder(Protocol):
    """Embeds text with one pinned model (ADR 0008)."""

    @property
    def tag(self) -> str:
        """The pinned model tag, stored with every corpus version."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class SearchHit:
    id: int
    title: str
    content: str
    score: float


@dataclass(frozen=True)
class ActiveCorpus:
    version: int
    embed_model_tag: str


class VectorStore(Protocol):
    """Read side of the corpus, used by the API."""

    async def active_corpus(self) -> ActiveCorpus | None: ...

    async def search(self, vector: list[float], text: str, k: int, version: int) -> list[SearchHit]:
        """Hybrid in one query (vector + full text + RRF), over the given version only."""


class Reranker(Protocol):
    """One rerank level; it may raise, and the retrieval service falls to the next level (ADR 0009)."""

    async def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]: ...

    def warm(self) -> None:
        """Load what the level needs, at startup; may raise."""


class ProviderError(Exception):
    """A model provider failed the call: transport error, 5xx, an unexpected status or a malformed body.

    The message names the kind of failure, never the request or the response body.
    """


class RateLimited(ProviderError):
    """The provider answered 429."""


class ContextTooLong(ProviderError):
    """The provider refused the prompt as longer than the model's context."""


@dataclass(frozen=True)
class RateInfo:
    """A provider's request quota for the day, from its `x-ratelimit-*` headers."""

    remaining: int
    limit: int


@dataclass(frozen=True)
class ChatResult:
    text: str
    tokens_in: int
    tokens_out: int
    rate: RateInfo | None = None


@dataclass(frozen=True)
class ChatChunk:
    """One piece of a streamed answer: text, or (last) the token counts."""

    text: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None


@dataclass(frozen=True)
class RerankResult:
    scores: list[float]  # one per document, in the documents' order
    rate: RateInfo | None = None


class ModelProvider(Protocol):
    """One OpenAI-compatible endpoint (ADR 0007); failures raise `ProviderError`; the caller bounds time."""

    async def chat(
        self, model: str, messages: list[dict[str, str]], *, max_tokens: int, schema: dict[str, Any] | None
    ) -> ChatResult: ...

    def chat_stream(
        self, model: str, messages: list[dict[str, str]], *, max_tokens: int
    ) -> AsyncIterator[ChatChunk]: ...

    async def rerank(self, model: str, query: str, documents: list[str]) -> RerankResult: ...

    async def close(self) -> None: ...


BudgetState = Literal["normal", "paid_disabled", "degraded"]


class Budget(Protocol):
    """The day's budget row (LLD "6.2 Daily budget"); the rules are the gateway's, the statements are here.

    A store that fails raises `StoreUnavailable`.
    """

    async def state(self, day: date) -> BudgetState: ...

    async def reserve(self, day: date, estimate: float, cap: float) -> bool:
        """Add the estimate, and count the call, only when the spend stays within `cap` and the day's paid
        entries aren't disabled; one statement, so concurrent reservations can't cross the cap."""

    async def settle(self, day: date, delta: float, cutoff: float, cap: float, now: datetime) -> None:
        """Add `delta` (actual - estimate); set `paid_disabled_at` / `degraded_at` when the settled spend
        reaches `cutoff` / `cap`."""


AlertKind = Literal[
    "budget_exhausted", "providers_down", "backup_failed", "restore_failed", "eval_regression"
]


class AlertStore(Protocol):
    """`alert_states` rows (LLD "6.10 Alerts"); a store that fails raises `StoreUnavailable`."""

    async def open(
        self, kind: AlertKind, now: datetime, remind_before: datetime
    ) -> Literal["opened", "remind"] | None:
        """Open the incident, or mark a reminder when the last message is older than `remind_before`; None
        when the incident is open and was told recently. One conditional statement."""

    async def close(self, kind: AlertKind, now: datetime) -> bool:
        """Close an open incident; False when none was open."""


class Alerter(Protocol):
    """Delivers one message (Telegram in production, a log line locally); may raise."""

    async def send(self, kind: AlertKind, message: str) -> None: ...

    async def close(self) -> None: ...


class EventLog(Protocol):
    """`log_events` rows for events that belong to no request (breaker transitions)."""

    def write(self, level: str, component: str, event: str, fields: dict[str, Any]) -> None:
        """Never blocks: the row is written in the background, and a failure is only logged."""

    async def drain(self) -> None:
        """Wait for the pending writes, at shutdown and in tests."""


@dataclass(frozen=True)
class DocumentRow:
    doc_key: str
    title: str
    kind: str
    lang: str
    authority: int
    source_url: str | None
    updated: str | None
    hash: str


@dataclass(frozen=True)
class ChunkRow:
    doc_key: str
    heading_path: str
    content: str
    tokens: int
    hash: str
    embedding: list[float]


@dataclass(frozen=True)
class PublishedChunk:
    id: int
    doc_key: str
    title: str
    kind: str
    lang: str
    heading: str
    text: str
    source_url: str | None
    embedding: list[float]


class CorpusStore(Protocol):
    """Write side of the corpus, used by ingest (LLD "6.1 Corpus version")."""

    def lock(self) -> AbstractAsyncContextManager[bool]:
        """Hold the one-ingest-at-a-time lock; yields False when another run holds it."""

    async def create_candidate(self, embed_model_tag: str) -> int: ...

    async def reject(self, version: int) -> None: ...

    async def reusable_embeddings(self, hashes: list[str], embed_model_tag: str) -> dict[str, list[float]]:
        """Embeddings of chunks with these hashes in any version with the same model tag."""

    async def write(self, version: int, documents: list[DocumentRow], chunks: list[ChunkRow]) -> None:
        """Documents and chunks of a candidate, in one transaction; chunks are idempotent by hash."""

    async def embed_model_tag(self, version: int) -> str | None:
        """The version's model tag, or None when it doesn't exist."""

    async def eval_passed(self, version: int) -> bool:
        """Whether the version has a linked eval run that passed."""

    async def activate(self, version: int) -> None:
        """The candidate becomes active and the previous active retired, in one transaction.

        Raises LookupError when the version isn't a candidate with documents.
        """

    async def published_chunks(self, version: int) -> list[PublishedChunk]: ...


class TraceSink(Protocol):
    """Receives the step events that feed the stream (and, later, the stored trace and LangSmith)."""

    def record(self, event: StreamEvent) -> None:
        """Never blocks."""


class DataPublisher(Protocol):
    """Writes the brain and corpus data the front reads (LLD "Published data")."""

    async def publish(self, folder: str, files: dict[str, bytes], manifest: bytes) -> None:
        """Write `files` under `folder`, then replace the manifest; idempotent."""


@dataclass(frozen=True)
class Finding:
    file: str
    rule: str


class SecretScanner(Protocol):
    """Finds secrets in a folder; a finding names the file and the rule, never the match."""

    async def scan(self, folder: Path) -> list[Finding]: ...


__all__ = [
    "EMBEDDING_DIM",
    "ActiveCorpus",
    "AlertKind",
    "AlertStore",
    "Alerter",
    "Budget",
    "BudgetState",
    "ChatChunk",
    "ChatResult",
    "ChunkRow",
    "ContextTooLong",
    "CorpusStore",
    "DataPublisher",
    "DocumentRow",
    "Embedder",
    "EventLog",
    "Finding",
    "HealthProbe",
    "ModelProvider",
    "ProviderError",
    "PublishedChunk",
    "RateInfo",
    "RateLimited",
    "RerankResult",
    "Reranker",
    "SearchHit",
    "SecretScanner",
    "TraceSink",
    "VectorStore",
]
