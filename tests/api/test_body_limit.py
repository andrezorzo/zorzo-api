"""The body cap's answer to a client that leaves mid-body (LLD "Errors")."""

from rag_api.body_limit import CLIENT_CLOSED, BodyLimit
from starlette.types import Message


async def test_a_client_that_leaves_mid_body_is_answered_499() -> None:
    reached = False

    async def app(scope: object, receive: object, send: object) -> None:
        nonlocal reached
        reached = True

    incoming: list[Message] = [
        {"type": "http.request", "body": b'{"q": "x', "more_body": True},
        {"type": "http.disconnect"},
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/v1/ask", "headers": [], "state": {}}
    await BodyLimit(app, limit=lambda: 8192)(scope, receive, send)
    assert not reached
    assert sent[0]["status"] == CLIENT_CLOSED
