"""Health check (LLD "HTTP routes", `GET /api/healthz`)."""

from dataclasses import dataclass
from typing import Literal

import structlog

from zorzo_core.ports import HealthProbe

log = structlog.get_logger()


@dataclass(frozen=True)
class HealthReport:
    status: Literal["ok", "down"]
    mode: Literal["normal", "degraded", "unknown"]
    db: Literal["ok", "down"]

    @property
    def healthy(self) -> bool:
        return self.status == "ok"


async def check(probe: HealthProbe) -> HealthReport:
    try:
        await probe.ping()
        degraded = await probe.degraded_today()
    except Exception:
        log.warning("health_check_failed", exc_info=True)
        # The mode lives in the database: without it, it can't be known.
        return HealthReport(status="down", mode="unknown", db="down")
    return HealthReport(status="ok", mode="degraded" if degraded else "normal", db="ok")
