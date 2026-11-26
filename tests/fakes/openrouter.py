"""A stand-in for OpenRouter's OpenAI-compatible API (AC6), as an httpx `MockTransport`.

Scripted per model: each model has a queue of behaviors, consumed one per request; an empty queue answers
normally. It records every request (path, model, body), so a test can assert which chain entries were called.

- chat completions, as JSON or SSE (`stream: true`), with usage;
- `/rerank`, scoring the documents in their given order unless `rerank_scores` says otherwise;
- failures: "rate_limited" (429 with `Retry-After`), "error" (500), "bad_gateway" (502), "unavailable"
  (503), "bad_request" (400), "slow_error" (500 after 0.25 s), "hang", "stall_after_first_token", "drip",
  "keepalive_only" and "reasoning" (after one word: silence, a word every 50 ms, keep-alive comments only, or
  reasoning frames without text), "reasoning_only" (reasoning frames and never any text),
  "whitespace_stall" (a line break, then silence), "no_text" (a stream that ends with no text), "malformed" (a
  200 that isn't the expected JSON), "context_too_long" (400), "break_after_first_token" (a stream that dies
  mid-way);
- `Reply(...)` for a given text, token counts or rate-limit headers.
"""

import asyncio
import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_TEXT = "Hello from the fake model"
DRIP_TEXT = " ".join(["word"] * 40)  # 2 s at one word every 50 ms
# Failures answered with an error status: 500 and 502 may come after the model ran, 503 and 400 before.
STATUS = {"error": 500, "bad_gateway": 502, "unavailable": 503, "bad_request": 400}


@dataclass(frozen=True)
class Reply:
    text: str = DEFAULT_TEXT
    tokens_in: int = 100
    tokens_out: int = 20
    headers: dict[str, str] = field(default_factory=dict)


Behavior = str | Reply


@dataclass(frozen=True)
class Recorded:
    path: str
    model: str
    body: dict[str, Any]


class FakeOpenRouter:
    def __init__(self) -> None:
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
        if behavior == "slow_error":  # a 500 after 0.25 s
            await asyncio.sleep(0.25)
            return httpx.Response(500, json={"error": {"code": 500}})
        if behavior == "malformed":
            return httpx.Response(200, text="<html>")
        if behavior == "context_too_long":
            message = "This endpoint's maximum context length is 8192 tokens."
            return httpx.Response(400, json={"error": {"code": 400, "message": message}})
        reply = behavior if isinstance(behavior, Reply) else Reply()
        if path == "/rerank":
            return self._rerank(body, reply)
        if body.get("stream"):
            return self._stream(reply, broken=behavior == "break_after_first_token", pace=behavior)
        return httpx.Response(
            200,
            headers=reply.headers,
            json={
                "id": "gen-1",
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": reply.text}}],
                "usage": {"prompt_tokens": reply.tokens_in, "completion_tokens": reply.tokens_out},
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
            headers=reply.headers,
            json={"model": body["model"], "results": results, "usage": {"total_tokens": 10, "cost": 0}},
        )

    def _stream(self, reply: Reply, broken: bool, pace: Behavior) -> httpx.Response:
        words = (DRIP_TEXT if pace == "drip" else reply.text).split(" ")

        async def frames() -> AsyncIterator[bytes]:
            yield b": OPENROUTER PROCESSING\n\n"
            if pace == "whitespace_stall":  # a line break, then nothing
                yield _frame({"content": "\n"})
                await asyncio.sleep(60)
                return
            if pace == "no_text":  # a role frame, then the end
                yield _frame({"role": "assistant"})
                yield b"data: [DONE]\n\n"
                return
            if pace == "reasoning_only":  # thinks for 5 s, never writes
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    thought = {"choices": [{"index": 0, "delta": {"reasoning": "..."}}]}
                    yield f"data: {json.dumps(thought)}\n\n".encode()
                return
            for n, word in enumerate(words):
                text = word if n == 0 else " " + word
                frame = {"choices": [{"index": 0, "delta": {"content": text}}]}
                yield f"data: {json.dumps(frame)}\n\n".encode()
                if broken:
                    raise httpx.ReadError("connection reset")
                if pace == "stall_after_first_token":
                    await asyncio.sleep(60)
                if pace == "keepalive_only":  # the aggregator's comments, the model silent
                    for _ in range(100):
                        await asyncio.sleep(0.05)
                        yield b": OPENROUTER PROCESSING\n\n"
                if pace == "reasoning":  # the model thinks: frames with no text
                    for _ in range(100):
                        await asyncio.sleep(0.05)
                        thought = {"choices": [{"index": 0, "delta": {"reasoning": "..."}}]}
                        yield f"data: {json.dumps(thought)}\n\n".encode()
                if pace == "drip":  # one word every 50 ms
                    await asyncio.sleep(0.05)
            usage = {"prompt_tokens": reply.tokens_in, "completion_tokens": reply.tokens_out}
            yield f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n".encode()
            yield b"data: [DONE]\n\n"

        return httpx.Response(
            200, headers={"content-type": "text/event-stream", **reply.headers}, content=frames()
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


def _frame(delta: dict[str, str]) -> bytes:
    return f"data: {json.dumps({'choices': [{'index': 0, 'delta': delta}]})}\n\n".encode()
