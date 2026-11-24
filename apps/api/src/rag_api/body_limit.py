"""Request body cap (LLD "Security", input): a body over `max_body_bytes` is refused before it is parsed.

Bytes are counted as they arrive, so a body sent without `Content-Length` (chunked) is capped too. The body
is read here, up to the limit, and replayed to the app; nothing over the limit is ever buffered.
"""

from collections.abc import Callable

from fastapi.responses import JSONResponse
from rag_core.errors import DEFAULT_MESSAGE, STATUS, ErrorCode, error_body
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyLimit:
    def __init__(self, app: ASGIApp, limit: Callable[[], int]) -> None:
        self.app = app
        self.limit = limit  # read per request, so the value follows `app.state.limits`

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit()
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._refuse(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # The client left mid-body. An answer goes nowhere, but every request must end with one, or
                # the middleware above would log the missing response as a server error.
                await self._refuse(scope, receive, send)
                return
            chunks.append(message.get("body", b""))
            size += len(chunks[-1])
            if size > limit:
                await self._refuse(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
        code = ErrorCode.INVALID_INPUT
        trace_id = str(scope.get("state", {}).get("trace_id", ""))
        response = JSONResponse(error_body(code, DEFAULT_MESSAGE[code], trace_id), status_code=STATUS[code])
        await response(scope, receive, send)
