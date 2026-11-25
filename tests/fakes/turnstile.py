"""A stand-in for Turnstile's `siteverify` that answers as Cloudflare documents its test keys (AC6).

The always-pass secret accepts any token (the real endpoint does, whatever its pages say about the dummy
token); the always-fail secret refuses every one. `tests/contract/test_turnstile_live.py` checks the real
endpoint gives the same answers, so this stand-in can't drift from it unnoticed.
"""

import asyncio
from urllib.parse import parse_qs

import httpx

PASS_SECRET = "1x0000000000000000000000000000000AA"  # always passes
FAIL_SECRET = "2x0000000000000000000000000000000AA"  # always fails
DUMMY_TOKEN = "XXXX.DUMMY.TOKEN.XXXX"  # what the test site keys produce


class FakeSiteverify:
    """Answers like Cloudflare; `mode` makes it fail: "error" (HTTP 500), "hang" or "garbage" (not JSON)."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.calls = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.mode == "error":
            return httpx.Response(500, json={"success": False, "error-codes": ["internal-error"]})
        if self.mode == "hang":
            await asyncio.sleep(60)
        if self.mode == "garbage":
            return httpx.Response(200, text="<html>")
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        secret, token = form.get("secret"), form.get("response")
        if not token:
            return httpx.Response(200, json={"success": False, "error-codes": ["missing-input-response"]})
        if secret == PASS_SECRET:
            return httpx.Response(200, json={"success": True, "error-codes": [], "hostname": "example.com"})
        if secret == FAIL_SECRET:
            return httpx.Response(200, json={"success": False, "error-codes": ["invalid-input-response"]})
        return httpx.Response(200, json={"success": False, "error-codes": ["invalid-input-secret"]})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)
