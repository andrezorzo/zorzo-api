"""Health check (LLD "HTTP routes", `GET /api/healthz`)."""

import asyncio
from dataclasses import dataclass
from typing import Literal

import structlog

from rag_core.ports import HealthProbe
from rag_core.services.gateway import ModelGateway

log = structlog.get_logger()


@dataclass(frozen=True)
class HealthReport:
    status: Literal["ok", "down"]
    mode: Literal["normal", "degraded", "unknown"]
    db: Literal["ok", "down"]

    @property
    def healthy(self) -> bool:
        return self.status == "ok"


MODE_TIMEOUT_S = 2.0


async def check(probe: HealthProbe, gateway: ModelGateway) -> HealthReport:
    """`degraded` whenever a question would go search-only for a reason the gateway gives (budget at the cap,
    or every free `answer` entry cooling down while the paid entry can't run)."""
    try:
        await probe.ping()
        async with asyncio.timeout(MODE_TIMEOUT_S):
            reason = await gateway.mode()
    except Exception:
        log.warning("health_check_failed", exc_info=True)
        # The budget lives in the database: without it, the mode can't be known.
        return HealthReport(status="down", mode="unknown", db="down")
    return HealthReport(status="ok", mode="normal" if reason is None else "degraded", db="ok")
