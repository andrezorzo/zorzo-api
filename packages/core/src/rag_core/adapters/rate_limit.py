"""Token buckets in Postgres (`rate_buckets`, LLD "6.7 Rate limit" and "Concurrency and idempotency").

One multi-row upsert takes a token only from the buckets that have one, and locks their rows; when any
bucket was empty, the transaction rolls back, so a refused question takes nothing. Rows go in key order, so
two requests that share buckets lock them in the same order and can't deadlock.
"""

from datetime import datetime

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION, TEXT
from sqlalchemy.exc import SQLAlchemyError

from rag_core.adapters.postgres import PROBE_TIMEOUT_S, Database
from rag_core.ports import Bucket, StoreUnavailable

# The refilled level of a stored bucket at :now; `i` is the bucket's input row.
_LEVEL = """least(i.capacity, b.tokens
    + greatest(0, extract(epoch FROM CAST(:now AS timestamptz) - b.refilled_at)) * i.refill_per_s)"""

# The buckets of one take as rows: key, capacity, refill per second.
_INPUT = "WITH i AS (SELECT * FROM unnest(:keys, :capacities, :refills) AS i(key, capacity, refill_per_s))"
_ARRAYS = (
    bindparam("keys", type_=ARRAY(TEXT)),
    bindparam("capacities", type_=ARRAY(DOUBLE_PRECISION)),
    bindparam("refills", type_=ARRAY(DOUBLE_PRECISION)),
)

TAKE = text(f"""
{_INPUT}
INSERT INTO rate_buckets AS b (key, tokens, refilled_at)
SELECT key, capacity - 1, :now FROM i ORDER BY key
ON CONFLICT (key) DO UPDATE
SET tokens = (SELECT {_LEVEL} FROM i WHERE i.key = b.key) - 1,
    refilled_at = greatest(b.refilled_at, EXCLUDED.refilled_at)
WHERE (SELECT {_LEVEL} FROM i WHERE i.key = b.key) >= 1
RETURNING b.key
""").bindparams(*_ARRAYS)

# A take touches a few rows: Postgres itself ends a statement that waits on a lock or runs long, so a lock
# wait fails in seconds. A connection that stops answering altogether is bounded only by the caller's deadline
# (cancelling a query on a dead socket waits on that socket).
BOUNDS = text("SELECT set_config('statement_timeout', :ms, true), set_config('lock_timeout', :ms, true)")

LEVELS = text(f"""
{_INPUT}
SELECT b.key, {_LEVEL} FROM rate_buckets b JOIN i ON i.key = b.key
""").bindparams(*_ARRAYS)


class PostgresRateLimiter:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def take(self, buckets: list[Bucket], now: datetime) -> list[tuple[Bucket, float]] | None:
        """See `RateLimiter.take`."""
        try:
            return await self._take(buckets, now)
        except (OSError, SQLAlchemyError) as exc:
            raise StoreUnavailable(type(exc).__name__) from exc

    async def _take(self, buckets: list[Bucket], now: datetime) -> list[tuple[Bucket, float]] | None:
        params = {
            "keys": [b.key for b in buckets],
            "capacities": [float(b.capacity) for b in buckets],
            "refills": [b.refill_per_s for b in buckets],
            "now": now,
        }
        bounds = {"ms": str(int(PROBE_TIMEOUT_S * 1000))}
        async with self.db.engine.connect() as conn:
            async with conn.begin() as tx:
                await conn.execute(BOUNDS, bounds)
                taken = (await conn.execute(TAKE, params)).scalars().all()
                if len(taken) == len(buckets):
                    return None
                await tx.rollback()
            async with conn.begin():
                await conn.execute(BOUNDS, bounds)
                levels = {key: level for key, level in (await conn.execute(LEVELS, params)).all()}
        # A bucket refilled between the rollback and this read isn't listed; the list may then be empty.
        return [(b, _wait(b, levels[b.key], now)) for b in buckets if levels.get(b.key, 1) < 1]


def _wait(bucket: Bucket, level: float, now: datetime) -> float:
    """Seconds until the bucket holds one token: a refill, or its reset for one that doesn't refill."""
    if bucket.refill_per_s > 0:
        return (1 - level) / bucket.refill_per_s
    assert bucket.resets_at is not None, "a bucket that doesn't refill needs resets_at"
    return max(0.0, (bucket.resets_at - now).total_seconds())
