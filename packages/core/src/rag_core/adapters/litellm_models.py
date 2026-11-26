"""The model calls on LiteLLM (ADR 0016, LLD "6.6 Circuit breaker"): one Router for the free entries of every
stage, `acompletion` for each stage's paid entry.

Each stage is one Router model group; its free entries are deployments tried in chain order (`order`), with no
same-entry retry (`num_retries=0`: the next entry is the retry, and a retry would wait out a 429's
`Retry-After`). The Router's cooldowns replace a breaker of our own. Failures come out as `ProviderError`
(`refused` when the provider turned the call down) or `TimeoutError`; the message names the kind, never the
request or the response.
"""

import os

# Set before LiteLLM is imported: otherwise the import fetches the model cost map from GitHub.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, cast

import litellm
import openai
from litellm.exceptions import (
    ContextWindowExceededError,
    MidStreamFallbackError,
    RateLimitError,
    Timeout,
)
from litellm.llms.custom_httpx.async_client_cleanup import close_litellm_async_clients
from litellm.router import Router
from litellm.router_utils.cooldown_handlers import (
    async_get_cooldown_deployments_with_debug_info,
)
from litellm.types.router import RouterRateLimitError

from rag_core.models_config import Entry, ModelsConfig, Provider, StageName, entry_id
from rag_core.ports import (
    ChatResult,
    ContextTooLongError,
    Cooldown,
    ProviderError,
    RateLimitedError,
)

# LiteLLM prints a "Give Feedback / Get Help" banner to stdout on every error unless told not to.
litellm.suppress_debug_info = True

STAGES: tuple[StageName, ...] = ("route", "rerank", "answer", "judge")
KEYLESS = "unused"  # the OpenAI client needs some key; a keyless endpoint (Ollama) ignores it
_CONTEXT_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "too many tokens",
    "context window",
)
_LIBRARY_ERRORS = (openai.OpenAIError, RouterRateLimitError)


class LiteLLMModels:
    def __init__(self, config: ModelsConfig, keys: Mapping[str, str | None], cooldown_s: float) -> None:
        """`keys` maps each provider's name to its key (None for a keyless one)."""
        self._paid: dict[str, tuple[str, dict[str, Any]]] = {}  # stage → (entry id, LiteLLM params)
        deployments: list[dict[str, Any]] = []
        for stage in STAGES:
            chain = config.stage(stage)
            for order, entry in enumerate(chain.free, start=1):
                params = _params(stage, entry, config.providers[entry.provider], keys.get(entry.provider))
                deployments.append(
                    {
                        "model_name": stage,
                        "litellm_params": {**params, "order": order},
                        "model_info": {"id": entry_id(stage, entry)},
                    }
                )
            if chain.paid is not None:
                paid = chain.paid
                params = _params(stage, paid, config.providers[paid.provider], keys.get(paid.provider))
                self._paid[stage] = (entry_id(stage, paid), params)
        self._router = Router(model_list=deployments, num_retries=0, cooldown_time=cooldown_s)

    async def complete(
        self,
        stage: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        schema: dict[str, Any] | None,
        timeout: float,
    ) -> ChatResult:
        call = self._router.acompletion(
            model=stage, **_request(messages, max_tokens, timeout), **_format(schema)
        )
        return await _complete(call, entry=None)

    def stream(
        self, stage: str, messages: list[dict[str, str]], *, max_tokens: int, timeout: float
    ) -> AsyncIterator[str | ChatResult]:
        return _stream(
            lambda: self._router.acompletion(
                model=stage, **_request(messages, max_tokens, timeout, stream=True)
            ),
            entry=None,
        )

    async def complete_paid(
        self,
        stage: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        schema: dict[str, Any] | None,
        timeout: float,
    ) -> ChatResult:
        entry, params = self._paid_entry(stage)
        call = litellm.acompletion(
            **params, num_retries=0, **_request(messages, max_tokens, timeout), **_format(schema)
        )
        return await _complete(call, entry)

    def stream_paid(
        self, stage: str, messages: list[dict[str, str]], *, max_tokens: int, timeout: float
    ) -> AsyncIterator[str | ChatResult]:
        entry, params = self._paid_entry(stage)
        return _stream(
            lambda: litellm.acompletion(
                **params, num_retries=0, **_request(messages, max_tokens, timeout, stream=True)
            ),
            entry,
        )

    async def rerank(self, query: str, documents: list[str], *, timeout: float) -> list[float]:
        try:
            response = await self._router.arerank(
                model="rerank", query=query, documents=documents, timeout=timeout
            )
        except _LIBRARY_ERRORS as exc:
            raise _provider_error(exc) from None
        scores: dict[int, float] = {}
        for item in response.results or []:
            index, score = item.get("index"), item.get("relevance_score")
            if isinstance(index, int) and isinstance(score, int | float) and 0 <= index < len(documents):
                scores[index] = float(score)
        if len(scores) != len(documents):
            raise ProviderError("incomplete rerank")
        return [scores[i] for i in range(len(documents))]

    async def cooldowns(self) -> dict[str, Cooldown]:
        active = await async_get_cooldown_deployments_with_debug_info(self._router, None)
        return {
            deployment: Cooldown(str(value["status_code"]), value["timestamp"], value["cooldown_time"])
            for deployment, value in active or []
        }

    async def close(self) -> None:
        self._router.discard()  # type: ignore[no-untyped-call]
        await close_litellm_async_clients()  # type: ignore[no-untyped-call]

    def _paid_entry(self, stage: str) -> tuple[str, dict[str, Any]]:
        if stage not in self._paid:
            raise ProviderError("no paid entry")
        return self._paid[stage]


def _params(stage: StageName, entry: Entry, provider: Provider, key: str | None) -> dict[str, Any]:
    """LiteLLM's model string and endpoint for an entry; a rerank is a Cohere-style `/rerank` on the base
    URL."""
    base = provider.base_url.rstrip("/")
    if stage == "rerank":
        return {"model": f"cohere/{entry.model}", "api_base": f"{base}/rerank", "api_key": key or KEYLESS}
    return {"model": f"{provider.kind}/{entry.model}", "api_base": base, "api_key": key or KEYLESS}


def _format(schema: dict[str, Any] | None) -> dict[str, Any]:
    if schema is None:
        return {}
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "output", "strict": True, "schema": schema},
        }
    }


def _request(
    messages: list[dict[str, str]], max_tokens: int, timeout: float, stream: bool = False
) -> dict[str, Any]:
    """The arguments every call shares; `messages` as LiteLLM types them (a union of TypedDicts)."""
    request: dict[str, Any] = {"messages": cast(Any, messages), "max_tokens": max_tokens, "timeout": timeout}
    if stream:
        request |= {"stream": True, "stream_timeout": timeout, "stream_options": {"include_usage": True}}
    return request


async def _complete(call: Awaitable[Any], entry: str | None) -> ChatResult:
    """One answer; `entry` is the paid entry's id, or None to take the free entry the Router chose."""
    try:
        response = await call
    except _LIBRARY_ERRORS as exc:
        raise _provider_error(exc) from None
    content = response.choices[0].message.content if response.choices else None
    if content is not None and not isinstance(content, str):
        raise ProviderError("unreadable content")
    usage = getattr(response, "usage", None)
    return ChatResult(
        content or "",
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
        entry or str(response._hidden_params.get("model_id") or ""),
        _cost(usage),
    )


async def _stream(call: Callable[[], Awaitable[Any]], entry: str | None) -> AsyncIterator[str | ChatResult]:
    """The text as it comes, then the `ChatResult`. A break after text is a failure that ran (and billed),
    whatever LiteLLM makes of it: this code knows text went out, the library's wrapper may not say so."""
    parts: list[str] = []
    usage: Any = None
    try:
        response = await call()
        async for piece in response:
            usage = getattr(piece, "usage", None) or usage
            text = piece.choices[0].delta.content if piece.choices else None
            if text is not None and not isinstance(text, str):
                raise ProviderError("unreadable content")
            if text:
                parts.append(text)
                yield text
    except _LIBRARY_ERRORS as exc:
        if parts:
            raise ProviderError("stream broke") from None
        raise _provider_error(exc) from None
    yield ChatResult(
        "".join(parts),
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
        entry or str((getattr(response, "_hidden_params", {}) or {}).get("model_id") or ""),
        _cost(usage),
    )


def _cost(usage: Any) -> float | None:
    """What OpenRouter reported it billed (`usage.cost`); None when it reported nothing."""
    reported = getattr(usage, "cost", None)
    try:
        return float(reported) if reported is not None else None
    except (TypeError, ValueError):
        return None


def _provider_error(exc: BaseException) -> Exception:
    """LiteLLM's error as ours: a timeout, a 429, a prompt too long, or a failure that was refused or may have
    run (LLD "6.2 Daily budget": only a refused call releases its reservation)."""
    if isinstance(exc, MidStreamFallbackError) and exc.original_exception is not None:
        # LiteLLM wraps a stream's failure, with status 503 when the original had none; the original says
        # what happened (a break after text is classified by `_stream`, which knows text went out).
        return _provider_error(exc.original_exception)
    if isinstance(exc, Timeout):
        return TimeoutError("timeout")
    if isinstance(exc, RateLimitError | RouterRateLimitError):
        return RateLimitedError()
    status = getattr(exc, "status_code", None)
    if isinstance(exc, ContextWindowExceededError) or (
        status in (400, 413) and any(m in str(exc).lower() for m in _CONTEXT_MARKERS)
    ):
        return ContextTooLongError(f"http {status}")
    if isinstance(status, int) and status != 200:
        # Refused up front: a redirect or a 4xx (but a 408 timeout), or a 503 (no provider available). A 500,
        # 502 or 504 may come after the upstream model ran, and billed.
        refused = (300 <= status < 500 and status != 408) or status == 503
        return ProviderError(f"http {status}", refused=refused)
    return ProviderError(type(exc).__name__)
