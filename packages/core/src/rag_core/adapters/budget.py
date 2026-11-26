"""The day's budget row in Postgres (`budget_daily`, LLD "6.2 Daily budget", "Concurrency and idempotency").

A reservation is one conditional upsert, so two concurrent requests can't both cross the cap; the settle
corrects the row by `actual - estimate` and records the state changes. Amounts go in as exact decimals with
the column's six places.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

from rag_core.adapters.postgres import Database, store_errors
from rag_core.ports import BudgetState

STATE = text("""
SELECT CASE WHEN degraded_at IS NOT NULL THEN 'degraded'
            WHEN paid_disabled_at IS NOT NULL THEN 'paid_disabled' ELSE 'normal' END
FROM budget_daily WHERE day = :day
""")

# A new day's row is inserted only when the estimate alone fits; an existing row is updated only when the
# spend stays within the cap and the day's paid entries are on.
RESERVE = text("""
INSERT INTO budget_daily AS b (day, cost_usd, requests)
SELECT :day, :estimate, 1 WHERE :estimate <= :cap
ON CONFLICT (day) DO UPDATE SET cost_usd = b.cost_usd + EXCLUDED.cost_usd, requests = b.requests + 1
WHERE b.cost_usd + EXCLUDED.cost_usd <= :cap AND b.paid_disabled_at IS NULL AND b.degraded_at IS NULL
RETURNING b.day
""")

SETTLE = text("""
UPDATE budget_daily SET
    cost_usd = greatest(0, cost_usd + :delta),
    paid_disabled_at = CASE WHEN paid_disabled_at IS NULL AND cost_usd + :delta >= :cutoff
                            THEN :now ELSE paid_disabled_at END,
    degraded_at = CASE WHEN degraded_at IS NULL AND cost_usd + :delta >= :cap THEN :now ELSE degraded_at END
WHERE day = :day
""")


def _usd(amount: float) -> Decimal:
    return Decimal(f"{amount:.6f}")


class PostgresBudget:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def state(self, day: date) -> BudgetState:
        # One read, on every question: no transaction or bounds around it (its callers bound the time).
        async with store_errors(), self.db.engine.connect() as conn:
            found = (await conn.execute(STATE, {"day": day})).scalar()
        return found or "normal"

    async def reserve(self, day: date, estimate: float, cap: float) -> bool:
        params = {"day": day, "estimate": _usd(estimate), "cap": _usd(cap)}
        async with self.db.short_transaction() as conn:
            return (await conn.execute(RESERVE, params)).first() is not None

    async def settle(self, day: date, delta: float, cutoff: float, cap: float, now: datetime) -> None:
        params = {"day": day, "delta": _usd(delta), "cutoff": _usd(cutoff), "cap": _usd(cap), "now": now}
        async with self.db.short_transaction() as conn:
            await conn.execute(SETTLE, params)
