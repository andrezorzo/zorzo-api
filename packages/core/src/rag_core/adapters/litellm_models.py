"""The model calls on LiteLLM (ADR 0016, LLD "6.6 Circuit breaker"): one Router, one model group per chain
entry, called by the gateway entry by entry.

The Router makes each call (timeouts, the provider's quirks, the cost OpenRouter reports) and keeps the
cooldowns: a 429, a 401 or a 404 cools an entry at once, for `Retry-After` when the provider sends one;
another failure of the entry cools it on the `FAILS_BEFORE_COOLDOWN + 1`-th within the cooldown time.
LiteLLM leaves some of the entry's failures out of that count: a dropped connection, a stream's timeout
after it opened, a 402, a 5xx whose text names `APIConnectionError`, and what this adapter finds wrong in a
200. The adapter adds exactly those, through LiteLLM's own counting. A
failure the request caused (a prompt too long, a 400, a 403) never counts: a visitor's question must not cool
an entry. There is no same-entry retry (`num_retries=0`: the next entry is the retry) and no Router fallback
(the gateway walks the chain). Failures come out as `ProviderError` (`refused` when the provider turned the
call down) or `TimeoutError`; the message names the kind, never the request or the response.
"""

import math
import os
from contextlib import contextmanager

# Set before LiteLLM is imported: otherwise the import fetches the model cost map from GitHub.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from collections.abc import AsyncIterator, Callable, Coroutine, Iterator, Mapping
from http import HTTPStatus
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
    should_cooldown_based_on_allowed_fails_policy,
)
from litellm.types.router import AllowedFailsPolicy, RouterRateLimitError

from rag_core.models_config import Entry, ModelsConfig, Provider, StageName, entry_id
from rag_core.ports import ChatResult, ContextTooLongError, Cooldown, ProviderError, RateLimitedError

# LiteLLM prints a "Give Feedback / Get Help" banner to stdout on every error unless told not to.
litellm.suppress_debug_info = True

STAGES: tuple[StageName, ...] = ("route", "rerank", "answer", "judge")
KEYLESS = "unused"  # the OpenAI client needs some key; a keyless endpoint (Ollama) ignores it
FAILS_BEFORE_COOLDOWN = 2  # a timeout, a 5xx or a dropped connection cools an entry on the third
COOL_AT_ONCE = 0  # allowed fails for a 429, a 401 or a 404
MAX_TOKEN_COUNT = 10_000_000  # far past any context: a larger count is a provider bug, not a bill
_CONTEXT_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "too many tokens",
    "context window",
)
_CONTEXT_STATUSES = (HTTPStatus.BAD_REQUEST, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
_ERROR_FINISH = "error"  # OpenRouter's `finish_reason` when the upstream model failed mid-answer
_LIBRARY_ERRORS = (openai.OpenAIError, RouterRateLimitError)


class LiteLLMModels:
    def __init__(self, config: ModelsConfig, keys: Mapping[str, str | None], cooldown_s: float) -> None:
        """`keys` maps each provider's name to its key (None for a keyless one)."""
        deployments = [
            {
                "model_name": entry_id(stage, entry),
                "litellm_params": _params(
                    stage, entry, config.providers[entry.provider], keys.get(entry.provider)
                ),
                "model_info": {"id": entry_id(stage, entry)},
            }
            for stage in STAGES
            for entry in config.stage(stage).chain
        ]
        self._cooldown_s = cooldown_s
        self._router = Router(
            model_list=deployments,
            num_retries=0,
            cooldown_time=cooldown_s,
            allowed_fails=FAILS_BEFORE_COOLDOWN,
            allowed_fails_policy=AllowedFailsPolicy(
                RateLimitErrorAllowedFails=COOL_AT_ONCE,
                AuthenticationErrorAllowedFails=COOL_AT_ONCE,
                NotFoundErrorAllowedFails=COOL_AT_ONCE,
            ),
            # LiteLLM spares a group with one deployment and no fallback from the fail count for any error
            # its policy doesn't name (a 500 comes as a generic `APIError`). Naming each entry its own
            # fallback lifts that; every call passes `disable_fallbacks`, so the fallback itself never runs.
            fallbacks=[{d["model_name"]: [d["model_name"]]} for d in deployments],
        )

    async def complete(
        self,
        entry_key: str,
        /,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        schema: dict[str, Any] | None,
        timeout: float,
    ) -> ChatResult:
        with self._failures_counted(entry_key):
            try:
                response = await self._router.acompletion(
                    model=entry_key, **_request(messages, max_tokens, timeout), **_format(schema)
                )
            except _LIBRARY_ERRORS as exc:
                raise _provider_error(exc) from exc
            choice = response.choices[0] if response.choices else None
            if _failed_choice(choice):
                raise ProviderError("error choice")
            content = choice.message.content if choice is not None else None
            if content is not None and not isinstance(content, str):
                raise ProviderError("unreadable content")
        usage = getattr(response, "usage", None)
        tokens_in, tokens_out = _counts(usage)
        return ChatResult(content or "", tokens_in, tokens_out, entry_key, _cost(usage))

    def stream(
        self, entry_key: str, /, messages: list[dict[str, str]], *, max_tokens: int, timeout: float
    ) -> AsyncIterator[str | ChatResult]:
        request = _request(messages, max_tokens, timeout, stream=True)
        return self._stream(lambda: self._router.acompletion(model=entry_key, **request), entry_key)

    async def rerank(
        self, entry_key: str, /, query: str, documents: list[str], *, timeout: float
    ) -> list[float]:
        with self._failures_counted(entry_key):
            try:
                response = await self._router.arerank(
                    model=entry_key,
                    query=query,
                    documents=documents,
                    top_n=len(documents),  # every document back: providers default to fewer
                    timeout=timeout,
                    disable_fallbacks=True,
                )
            except _LIBRARY_ERRORS as exc:
                raise _provider_error(exc) from exc
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

    async def _stream(
        self, call: Callable[[], Coroutine[Any, Any, Any]], entry_key: str
    ) -> AsyncIterator[str | ChatResult]:
        """The text as it comes, then the `ChatResult`. A break after text is a failure that ran (and
        billed), whatever LiteLLM makes of it: this code knows text went out, the library's wrapper may not.

        The counts are reported but not billable: without a usage frame from the provider, LiteLLM fills them
        in from its own tokenizer, and nothing on the stream tells the two apart. An error choice on a stream
        isn't seen here (LiteLLM maps it to "stop" and keeps no trace): OpenRouter's error frames are caught
        by LiteLLM, which raises on them."""
        parts: list[str] = []
        usage: Any = None
        opened = [False]
        with self._failures_counted(entry_key, opened):
            try:
                response = await call()
                opened[0] = True
                async for piece in response:
                    usage = getattr(piece, "usage", None) or usage
                    choice = piece.choices[0] if piece.choices else None
                    text = choice.delta.content if choice is not None else None
                    if text is not None and not isinstance(text, str):
                        raise ProviderError("unreadable content")
                    if text:
                        parts.append(text)
                        yield text
            except _LIBRARY_ERRORS as exc:
                if parts:
                    raise ProviderError("stream broke") from exc
                raise _provider_error(exc) from exc
        tokens_in, tokens_out = _counts(usage)
        yield ChatResult(
            "".join(parts), tokens_in, tokens_out, entry_key, _cost(usage), billable_counts=False
        )

    @contextmanager
    def _failures_counted(self, entry_key: str, stream: list[bool] | None = None) -> Iterator[None]:
        """Add to the entry's cooldown count the failures LiteLLM leaves out (see the module docstring).
        `stream` holds one flag, set once the stream has opened."""
        try:
            yield
        except (ProviderError, TimeoutError) as exc:
            skipped = _skipped_by_litellm(exc, streaming=bool(stream and stream[0]))
            if skipped is not None and should_cooldown_based_on_allowed_fails_policy(
                self._router, entry_key, skipped
            ):
                self._router.cooldown_cache.add_deployment_to_cooldown(
                    model_id=entry_key,
                    original_exception=skipped,
                    exception_status=_status(skipped) or HTTPStatus.INTERNAL_SERVER_ERROR,
                    cooldown_time=self._cooldown_s,
                )
            raise


def _params(stage: StageName, entry: Entry, provider: Provider, key: str | None) -> dict[str, Any]:
    """LiteLLM's model string and endpoint for an entry; a rerank is a Cohere-style `/rerank` on the base
    URL."""
    base = provider.base_url.rstrip("/")
    if stage == "rerank":
        return {"model": f"cohere/{entry.model}", "api_base": f"{base}/rerank", "api_key": key or KEYLESS}
    return {"model": f"{provider.kind}/{entry.model}", "api_base": base, "api_key": key or KEYLESS}


def _request(
    messages: list[dict[str, str]], max_tokens: int, timeout: float, stream: bool = False
) -> dict[str, Any]:
    """The arguments every call shares; `messages` as LiteLLM types them (a union of TypedDicts)."""
    request: dict[str, Any] = {
        "messages": cast(Any, messages),
        "max_tokens": max_tokens,
        "timeout": timeout,
        "disable_fallbacks": True,
    }
    if stream:
        request |= {"stream": True, "stream_timeout": timeout, "stream_options": {"include_usage": True}}
    return request


def _format(schema: dict[str, Any] | None) -> dict[str, Any]:
    if schema is None:
        return {}
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "output", "strict": True, "schema": schema},
        }
    }


def _skipped_by_litellm(exc: BaseException, streaming: bool) -> Exception | None:
    """The failure to count, when it is the entry's and LiteLLM's cooldown accounting doesn't count it:
    - an error this adapter found in a 200 (no library error behind it);
    - a dropped connection (LiteLLM skips any error whose text names `APIConnectionError`);
    - once a stream is open, a timeout too (LiteLLM skips a stream's raw transport error: it has no status);
    - a 402 (skipped on a lone deployment);
    - a 5xx whose text names `APIConnectionError` (skipped for its text, which may quote the request).
    A request's own 4xx never counts, whatever its text."""
    cause = exc.__cause__
    if isinstance(cause, MidStreamFallbackError) and cause.original_exception is not None:
        cause = cause.original_exception
    if cause is None:
        return exc if isinstance(exc, Exception) else None
    if not isinstance(cause, Exception) or isinstance(cause, RouterRateLimitError):
        return None  # an entry already cooling: nothing new
    dropped = isinstance(cause, openai.APIConnectionError) and (
        streaming or not isinstance(cause, openai.APITimeoutError)
    )
    status = _status(cause)
    text_skipped = (
        status is not None
        and status >= HTTPStatus.INTERNAL_SERVER_ERROR
        and ("APIConnectionError" in str(cause))
    )
    if dropped or text_skipped or status == HTTPStatus.PAYMENT_REQUIRED:
        return cause
    return None


def _status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _failed_choice(choice: Any) -> bool:
    """OpenRouter's sign that the upstream model failed mid-answer. LiteLLM maps `finish_reason: "error"` to
    "stop", keeping the original in `provider_specific_fields`."""
    if choice is None:
        return False
    native = (getattr(choice, "provider_specific_fields", None) or {}).get("native_finish_reason")
    return _ERROR_FINISH in (choice.finish_reason, native)


def _counts(usage: Any) -> tuple[int, int]:
    """The token counts, or (0, 0) unless both are plausible: whole, non-negative and at most
    `MAX_TOKEN_COUNT` (0 makes a paid call settle at its estimate)."""
    prompt, completion = getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
    if _plausible(prompt) and _plausible(completion):
        return cast(int, prompt), cast(int, completion)
    return 0, 0


def _plausible(count: object) -> bool:
    return isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= MAX_TOKEN_COUNT


def _cost(usage: Any) -> float | None:
    """What OpenRouter reported it billed (`usage.cost`), when it is a finite, non-negative amount."""
    reported = getattr(usage, "cost", None)
    if isinstance(reported, bool) or not isinstance(reported, int | float):
        return None
    return float(reported) if math.isfinite(reported) and reported >= 0 else None


def _provider_error(exc: BaseException) -> Exception:
    """LiteLLM's error as ours: a timeout, a 429, a prompt too long, or a failure that was refused or may
    have run (LLD "6.2 Daily budget": only a refused call releases its reservation)."""
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
        status in _CONTEXT_STATUSES and any(m in str(exc).lower() for m in _CONTEXT_MARKERS)
    ):
        return ContextTooLongError(f"http {status}")
    if isinstance(status, int) and status != HTTPStatus.OK:
        return ProviderError(f"http {status}", refused=_refused(status))
    return ProviderError(type(exc).__name__)


def _refused(status: int) -> bool:
    """Turned down before the model ran: a redirect or a 4xx (but a 408 timeout), or a 503 (no provider
    available). A 500, 502 or 504 may come after the upstream model ran, and billed."""
    if status == HTTPStatus.SERVICE_UNAVAILABLE:
        return True
    redirect_or_client = HTTPStatus.MULTIPLE_CHOICES <= status < HTTPStatus.INTERNAL_SERVER_ERROR
    return redirect_or_client and status != HTTPStatus.REQUEST_TIMEOUT
