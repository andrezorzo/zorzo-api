"""Cloudflare Turnstile's server-side check (LLD "Security", bot check).

Only `success` is read: Cloudflare ties a site key to its hostnames, and the test keys answer `example.com`.
`remoteip` is not sent, so the raw IP stays inside the app. Any provider failure refuses the token, with no
retry: the gate fails closed.
"""

import asyncio

import httpx
import structlog

log = structlog.get_logger()

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
MAX_LOGGED_CODES = 5


class TurnstileBotCheck:
    def __init__(
        self, secret: str, timeout: float, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._secret = secret
        self._timeout = timeout
        # One client for the process, so checks reuse the connection; closed with the app.
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout)

    async def verify(self, token: str) -> bool:
        try:
            # The client's timeout is per operation; this one bounds the whole check.
            async with asyncio.timeout(self._timeout):
                response = await self._client.post(
                    SITEVERIFY_URL, data={"secret": self._secret, "response": token}
                )
            response.raise_for_status()
            body = response.json()
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            # The exception's text may quote the request; its type is enough to tell the failures apart.
            log.warning("bot_check_provider_error", error=type(exc).__name__)
            return False
        if isinstance(body, dict) and body.get("success") is True:
            return True
        codes = body.get("error-codes") if isinstance(body, dict) else None
        if not isinstance(codes, list):
            codes = []
        log.warning("bot_check_failed", codes=[str(c)[:40] for c in codes[:MAX_LOGGED_CODES]])
        return False

    async def close(self) -> None:
        await self._client.aclose()
