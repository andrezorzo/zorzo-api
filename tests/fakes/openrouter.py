"""A stand-in for OpenRouter's OpenAI-compatible API, served on a local port (`serve`), since LiteLLM builds
its own HTTP clients.

Scripted per model: each model has a queue of behaviors, consumed one per request; an empty queue answers
normally. It records every request (path, model, body), so a test can assert which chain entries were called.

- chat completions, as JSON or SSE (`stream: true`), with usage;
- `/rerank`, scoring the documents in their given order unless `rerank_scores` says otherwise;
- failures: "rate_limited" (429 with `Retry-After`), "error" (500), "bad_gateway" (502), "unavailable"
  (503), "bad_request" (400), "unauthorized" (401), "hang", "malformed" (a 200 that isn't the expected
  JSON), "context_too_long" (400), "no_text" (a stream that ends with no text), "break_after_first_token" (a
  stream that dies mid-way);
- `Reply(...)` for a given text, token counts or reported cost.
"""

import asyncio
import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from tests.fakes.server import FakeServer

DEFAULT_TEXT = "Hello from the fake model"
GEN_ID = "gen-1"
CREATED = 1_700_000_000
# Fields OpenRouter sends on every streamed frame (LiteLLM reads them).
FRAME_FIELDS = {"id": GEN_ID, "object": "chat.completion.chunk", "created": CREATED, "model": "fake"}
# Failures answered with an error status: 500 and 502 may come after the model ran, 503 and 400 before.
STATUS = {"error": 500, "bad_gateway": 502, "unavailable": 503, "bad_request": 400, "unauthorized": 401}


@dataclass(frozen=True)
class Reply:
    text: str = DEFAULT_TEXT
    tokens_in: int = 100
    tokens_out: int = 20
    cost: float | None = None  # what OpenRouter reports it billed (`usage.cost`)

    def usage(self) -> dict[str, Any]:
        usage: dict[str, Any] = {"prompt_tokens": self.tokens_in, "completion_tokens": self.tokens_out}
        return usage if self.cost is None else {**usage, "cost": self.cost}


Behavior = str | Reply


@dataclass(frozen=True)
class Recorded:
    path: str
    model: str
    body: dict[str, Any]


class FakeOpenRouter:
    def __init__(self) -> None:
        self._server: FakeServer | None = None
        self._base = ""
        self.scripts: dict[str, deque[Behavior]] = defaultdict(deque)
        self.requests: list[Recorded] = []
        # By default the documents keep their order; a test sets another ranking.
        self.rerank_scores: Callable[[list[str]], list[float]] = lambda docs: [
            float(len(docs) - i) for i in range(len(docs))
        ]

    def script(self, model: str, *behaviors: Behavior) -> None:
        self.scripts[model].extend(behaviors)

    def always(self, model: str, behavior: Behavior, times: int = 1000) -> None:
        self.script(model, *([behavior] * times))

    def models_called(self) -> list[str]:
        return [r.model for r in self.requests]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path.removeprefix("/api/v1")
        model = body.get("model", "")
        self.requests.append(Recorded(path, model, body))
        queue = self.scripts[model]
        behavior: Behavior = queue.popleft() if queue else Reply()
        if behavior == "rate_limited":
            return httpx.Response(429, headers={"Retry-After": "30"}, json={"error": {"code": 429}})
        if isinstance(behavior, str) and behavior in STATUS:
            return httpx.Response(STATUS[behavior], json={"error": {"code": STATUS[behavior]}})
        if behavior == "hang":
            await asyncio.sleep(60)
        if behavior == "malformed":
            return httpx.Response(200, text="<html>")
        if behavior == "context_too_long":
            message = "This endpoint's maximum context length is 8192 tokens."
            return httpx.Response(400, json={"error": {"code": 400, "message": message}})
        reply = behavior if isinstance(behavior, Reply) else Reply()
        if path == "/rerank":
            return self._rerank(body, reply)
        if body.get("stream"):
            return self._stream(reply, behavior)
        return httpx.Response(
            200,
            json={
                "id": GEN_ID,
                "object": "chat.completion",
                "created": CREATED,
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": reply.text}}],
                "usage": reply.usage(),
            },
        )

    def _rerank(self, body: dict[str, Any], reply: Reply) -> httpx.Response:
        scores = self.rerank_scores(body["documents"])
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[: body.get("top_n")]
        results = [
            {"index": i, "relevance_score": scores[i], "document": {"text": body["documents"][i]}}
            for i in ranked
        ]
        return httpx.Response(
            200,
            json={"model": body["model"], "results": results, "usage": {"total_tokens": 10, "cost": 0}},
        )

    def _stream(self, reply: Reply, behavior: Behavior) -> httpx.Response:
        async def frames() -> AsyncIterator[bytes]:
            yield b": OPENROUTER PROCESSING\n\n"
            if behavior == "no_text":  # a role frame, its usage, then the end
                yield _frame({"role": "assistant"})
                yield _data({"choices": [], "usage": {"prompt_tokens": 800, "completion_tokens": 0}})
                yield b"data: [DONE]\n\n"
                return
            for n, word in enumerate(reply.text.split(" ")):
                yield _frame({"content": word if n == 0 else " " + word})
                if behavior == "break_after_first_token":
                    raise httpx.ReadError("connection reset")
            yield _data({"choices": [], "usage": reply.usage()})
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=frames())

    def serve(self) -> str:
        """Start serving (once) and return the base URL a provider config uses (`…/api/v1`). Stopped by
        `stop_all`, which the tests' autouse fixture calls."""
        if self._server is None:
            self._server = FakeServer(self.handle)
            self._base = self._server.__enter__()
            _RUNNING.append(self._server)
        return f"{self._base}/api/v1"


_RUNNING: list[FakeServer] = []


def stop_all() -> None:
    while _RUNNING:
        _RUNNING.pop().__exit__(None, None, None)


def _data(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps({**FRAME_FIELDS, **payload})}\n\n".encode()


def _frame(delta: dict[str, str]) -> bytes:
    return _data({"choices": [{"index": 0, "delta": delta}]})
