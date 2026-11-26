"""Serves a fake's `httpx` handler over real HTTP on 127.0.0.1, for clients the tests can't give a transport.

LiteLLM builds its own HTTP clients, so the fake provider runs as a small ASGI app on a local port, in a
thread with its own event loop (LLD "10. Development environment").
"""

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import uvicorn

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]
STARTUP_TIMEOUT_S = 10.0


class _App:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        url = f"http://fake{scope['path']}"
        headers = [(k.decode(), v.decode()) for k, v in scope["headers"]]
        response = await self.handler(httpx.Request(scope["method"], url, headers=headers, content=body))
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": [
                    (k.encode(), v.encode())
                    for k, v in response.headers.items()
                    if k.lower() not in ("content-length", "transfer-encoding")
                ],
            }
        )
        try:
            body = response.content  # built in memory (JSON)
        except httpx.ResponseNotRead:  # a generator (SSE): relayed as it comes
            try:
                async for chunk in response.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            except httpx.ReadError:  # a scripted mid-stream break: drop the connection
                return
            body = b""
        await send({"type": "http.response.body", "body": body, "more_body": False})


class FakeServer:
    """`with FakeServer(handler) as base_url:` serves until the block ends."""

    def __init__(self, handler: Handler) -> None:
        # No graceful shutdown: a scripted hang would hold the test's teardown.
        config = uvicorn.Config(
            _App(handler),
            host="127.0.0.1",
            port=0,
            log_level="critical",
            lifespan="off",
            timeout_graceful_shutdown=0,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=lambda: asyncio.run(self._server.serve()), daemon=True)

    def __enter__(self) -> str:
        self._thread.start()
        started = threading.Event()

        def wait() -> None:
            while not self._server.started:
                threading.Event().wait(0.01)
            started.set()

        threading.Thread(target=wait, daemon=True).start()
        if not started.wait(STARTUP_TIMEOUT_S):
            raise RuntimeError("fake server did not start")
        port = self._server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = self._server.force_exit = True
        self._thread.join(STARTUP_TIMEOUT_S)
