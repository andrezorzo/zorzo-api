"""Ports: interfaces for what lives outside the core (LLD "Components and dependencies")."""

from typing import Protocol


class HealthProbe(Protocol):
    """What the health check needs from the database."""

    async def ping(self) -> None:
        """Raise when the database can't be reached."""

    async def degraded_today(self) -> bool:
        """Whether today's (UTC) budget row is in degraded mode."""
