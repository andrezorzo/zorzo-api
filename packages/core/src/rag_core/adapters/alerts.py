"""Alert adapters (LLD "6.10 Alerts"): the `alert_states` rows, Telegram in production, a log line locally."""

from datetime import datetime
from typing import Literal

import httpx
import structlog
from sqlalchemy import text

from rag_core.adapters.postgres import Database
from rag_core.ports import AlertKind

log = structlog.get_logger()

TELEGRAM_URL = "https://api.telegram.org"
TELEGRAM_TIMEOUT_S = 5.0

# Opens a closed (or new) incident, or takes the reminder slot when the last message is older than
# :remind_before; otherwise changes nothing and returns no row. One statement, so concurrent raises send once.
# A row whose `open_since` is :now was opened by this statement (a reminder needs time to have passed).
OPEN = text("""
INSERT INTO alert_states AS a (kind, open_since, last_sent_at, closed_at) VALUES (:kind, :now, :now, NULL)
ON CONFLICT (kind) DO UPDATE SET
    open_since = CASE WHEN a.open_since IS NULL OR a.closed_at IS NOT NULL THEN :now ELSE a.open_since END,
    last_sent_at = :now,
    closed_at = NULL
WHERE a.open_since IS NULL OR a.closed_at IS NOT NULL OR a.last_sent_at < :remind_before
RETURNING a.open_since = :now
""")

CLOSE = text("""
UPDATE alert_states SET closed_at = :now
WHERE kind = :kind AND open_since IS NOT NULL AND closed_at IS NULL
RETURNING kind
""")


class PostgresAlertStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def open(
        self, kind: AlertKind, now: datetime, remind_before: datetime
    ) -> Literal["opened", "remind"] | None:
        params = {"kind": kind, "now": now, "remind_before": remind_before}
        async with self.db.short_transaction() as conn:
            found = (await conn.execute(OPEN, params)).first()
        if found is None:
            return None
        return "opened" if found[0] else "remind"

    async def close(self, kind: AlertKind, now: datetime) -> bool:
        async with self.db.short_transaction() as conn:
            return (await conn.execute(CLOSE, {"kind": kind, "now": now})).first() is not None


class TelegramAlerter:
    """The bot token is part of Telegram's URL: the `httpx` loggers stay at `warning` (LLD "Logs"), and
    errors are reported by type only."""

    def __init__(self, token: str, chat_id: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._path = f"/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._client = httpx.AsyncClient(
            base_url=TELEGRAM_URL, transport=transport, timeout=TELEGRAM_TIMEOUT_S
        )

    async def send(self, kind: AlertKind, message: str) -> None:
        response = await self._client.post(
            self._path, json={"chat_id": self._chat_id, "text": f"{kind}: {message}"}
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()


class LogAlerter:
    """Local stand-in: the alert is a log line."""

    async def send(self, kind: AlertKind, message: str) -> None:
        log.warning("alert", kind=kind, message=message)

    async def close(self) -> None:
        pass
