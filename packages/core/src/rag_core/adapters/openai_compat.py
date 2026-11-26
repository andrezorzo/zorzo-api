"""One client for every OpenAI-compatible provider (ADR 0007): OpenRouter, Groq, NVIDIA NIM, Google AI Studio
and Ollama all speak the chat completions API; OpenRouter also answers `POST /rerank` (Cohere-style body).

httpx directly, no SDK: the gateway owns retries and timeouts (LLD "6.6 Circuit breaker"), so the client does
neither. Failures become `ProviderError` subclasses whose message names the kind of failure, never the
prompt or the response body.
"""

import json
import math
from collections.abc import AsyncIterator
from typing import Any

import httpx

from rag_core.ports import (
    ChatChunk,
    ChatResult,
    ContextTooLongError,
    ProviderError,
    RateInfo,
    RateLimitedError,
    RerankResult,
)

# A backstop only: the gateway bounds every call with min(TIMEOUT_<STAGE>, time left).
CLIENT_TIMEOUT_S = 60.0
_CONTEXT_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "too many tokens",
    "context window",
)


class OpenAICompatProvider:
    def __init__(
        self, base_url: str, key: str | None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            transport=transport,
            timeout=CLIENT_TIMEOUT_S,
        )

    async def chat(
        self, model: str, messages: list[dict[str, str]], *, max_tokens: int, schema: dict[str, Any] | None
    ) -> ChatResult:
        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "strict": True, "schema": schema},
            }
        response = await self._post("chat/completions", body)
        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, LookupError, TypeError) as exc:
            raise ProviderError("malformed body") from exc
        if not isinstance(choice, dict) or not isinstance(message, dict):
            raise ProviderError("malformed body")
        if _choice_failed(choice):
            raise ProviderError("error choice")
        text = _text(message.get("content"))
        if text is None:
            raise ProviderError("unreadable content")
        counts = _counts(data.get("usage")) or (0, 0)
        return ChatResult(text, *counts, _rate(response))

    async def chat_stream(
        self, model: str, messages: list[dict[str, str]], *, max_tokens: int
    ) -> AsyncIterator[ChatChunk]:
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            async with self._client.stream("POST", "chat/completions", json=body) as response:
                if response.status_code != 200:
                    await response.aread()
                    _raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        # Blank lines, and `: comment` keep-alives: those come from OpenRouter's edge, not
                        # from the model, so they say nothing about whether the model is still working.
                        continue
                    payload = line[len("data:") :].strip()
                    if payload == "[DONE]":
                        break
                    yield _stream_chunk(payload)
        except httpx.HTTPError as exc:
            raise ProviderError(type(exc).__name__) from exc

    async def rerank(self, model: str, query: str, documents: list[str]) -> RerankResult:
        body = {"model": model, "query": query, "documents": documents, "top_n": len(documents)}
        response = await self._post("rerank", body)
        try:
            results = response.json()["results"]
            scores = {int(r["index"]): float(r["relevance_score"]) for r in results}
        except (ValueError, LookupError, TypeError) as exc:
            raise ProviderError("malformed body") from exc
        if set(scores) != set(range(len(documents))):
            raise ProviderError("malformed body")
        return RerankResult([scores[i] for i in range(len(documents))], _rate(response))

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        try:
            response = await self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(type(exc).__name__) from exc
        _raise_for_status(response)
        return response


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status == 200:
        return
    if status == 429:
        raise RateLimitedError("http 429")
    if status in (400, 413) and any(m in response.text.lower() for m in _CONTEXT_MARKERS):
        raise ContextTooLongError(f"http {status}")
    # Refused up front: a redirect or a 4xx (but a 408 timeout), or a 503 (no provider available). Another
    # 2xx, a 500, 502 or 504 may come after the upstream model ran, and billed.
    refused = (300 <= status < 500 and status != 408) or status == 503
    raise ProviderError(f"http {status}", refused=refused)


def _stream_chunk(payload: str) -> ChatChunk:
    """One data frame: its text, if any, and its token counts, if any (some providers send them with text).

    A frame without text (a role or reasoning delta) still comes from the model, so it is still a chunk. A
    frame of the wrong shape is a "malformed frame", and one whose content isn't a string "unreadable
    content": both the provider's failure.
    """
    try:
        data = json.loads(payload)
        if isinstance(data, dict) and data.get("error"):  # a mid-stream failure, as OpenRouter sends it
            raise ProviderError("error frame")
        choices = data.get("choices")
        choices = [] if choices is None else choices
        if not isinstance(choices, list):
            raise ProviderError("malformed frame")
        choice = choices[0] if choices else None  # a usage-only frame may have no choice, or a null one
        if choice is not None and not isinstance(choice, dict):
            raise ProviderError("malformed frame")
        if isinstance(choice, dict) and _choice_failed(choice):
            raise ProviderError("error frame")
        delta = choice.get("delta") if isinstance(choice, dict) else None
        if delta is not None and not isinstance(delta, dict):
            raise ProviderError("malformed frame")
        text = _text(delta.get("content")) if delta is not None else ""
        if text is None:
            raise ProviderError("unreadable content")  # its own kind: a provider sending parts shows up
        usage = data.get("usage")  # some providers send it with the last text, or on every frame
        counts = _counts(usage)
        if counts is not None:
            return ChatChunk(text=text, tokens_in=counts[0], tokens_out=counts[1])
    except (ValueError, LookupError, TypeError, AttributeError) as exc:
        raise ProviderError("malformed frame") from exc
    return ChatChunk(text=text)


def _text(content: object) -> str | None:
    """The text of a message's or a delta's `content`, for both paths: a string is the text; absent or null
    (a filter, a refusal, the tokens spent on reasoning: some providers leave the key out) is "", the model's
    miss; any other shape (a list of parts, a dict, a number) is None, unreadable, the provider's failure.

    The chat completions format these providers speak sends assistant content as a string. Parts lists are
    not read until a provider in the config sends them, with its real format in hand.
    """
    if content is None:
        return ""
    return content if isinstance(content, str) else None


def _choice_failed(choice: dict[str, Any]) -> bool:
    """OpenRouter reports an upstream failure in the choice itself, with a 200."""
    return bool(choice.get("error")) or choice.get("finish_reason") == "error"


MAX_TOKEN_COUNT = 10_000_000  # far past any context: a larger count is a provider bug, not a bill


def _counts(usage: object) -> tuple[int, int] | None:
    """Both token counts of a `usage` object, or None when either is missing or isn't a whole, finite,
    plausible number of tokens: the caller then keeps its estimate, never a count that bills too little."""
    if not isinstance(usage, dict):
        return None
    counts = []
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        if not math.isfinite(value) or not 0 <= value <= MAX_TOKEN_COUNT or value != int(value):
            return None
        counts.append(int(value))
    return counts[0], counts[1]


def _rate(response: httpx.Response) -> RateInfo | None:
    try:
        return RateInfo(
            int(response.headers["x-ratelimit-remaining-requests"]),
            int(response.headers["x-ratelimit-limit-requests"]),
        )
    except (KeyError, ValueError):
        return None
