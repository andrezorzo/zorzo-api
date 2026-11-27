"""A stand-in for LangSmith's ingest endpoint, served on a local port: it records what the SDK posts, or hangs
(LLD "10. Development environment").

The SDK posts run operations as `multipart/form-data` to `/runs/multipart`, each part named
`<post|patch>.<run id>[.<field>]`; `runs()` folds them into one dict per run, in the order the runs were
first posted.
"""

import asyncio
import email
import json
from dataclasses import dataclass
from email.message import Message
from typing import Any, cast

import httpx

from tests.fakes.server import FakeServer

HANG_S = 60


@dataclass(frozen=True)
class Received:
    path: str
    body: bytes
    content_type: str


class FakeLangSmith:
    def __init__(self) -> None:
        self.mode = "ok"  # "hang": every request waits, as an unreachable endpoint does
        self.received: list[Received] = []
        self._server: FakeServer | None = None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.received.append(
            Received(request.url.path, request.content, request.headers.get("content-type", ""))
        )
        if self.mode == "hang":
            await asyncio.sleep(HANG_S)
        return httpx.Response(202, json={})

    def serve(self) -> str:
        """Start serving (once); the URL to give the SDK as its endpoint. `stop_all` stops it."""
        from tests.fakes.openrouter import _RUNNING  # one registry of running fakes, stopped after each test

        if self._server is None:
            self._server = FakeServer(self.handle)
            self.url = self._server.__enter__()
            _RUNNING.append(self._server)
        return self.url

    def text(self) -> str:
        """Everything received, as text, for a test that looks for what must not be there."""
        return "\n".join(r.body.decode("utf-8", errors="replace") for r in self.received)

    def runs(self) -> list[dict[str, Any]]:
        """One dict per run, its posted and patched fields merged."""
        runs: dict[str, dict[str, Any]] = {}
        for received in self.received:
            for name, value in _parts(received):
                _, run_id, *field = name.split(".", 2)
                run = runs.setdefault(run_id, {})
                if field:
                    run[field[0]] = value
                elif isinstance(value, dict):
                    run.update(value)
        return list(runs.values())


def _parts(received: Received) -> list[tuple[str, Any]]:
    message = email.message_from_bytes(
        b"Content-Type: " + received.content_type.encode() + b"\r\n\r\n" + received.body
    )
    found = []
    parts = cast(list[Message], message.get_payload()) if message.is_multipart() else []
    for part in parts:
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        if isinstance(name, str) and isinstance(payload, bytes) and name.split(".")[0] in ("post", "patch"):
            try:
                found.append((name, json.loads(payload)))
            except ValueError:
                continue  # an attachment, not a run field
    return found
