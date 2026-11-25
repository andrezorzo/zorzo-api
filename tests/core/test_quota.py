"""AC2: free quota from the provider's headers, trusted for a minute (LLD "6.8 Free quota")."""

from pathlib import Path

from rag_core.limits import load_limits
from rag_core.models_config import load_models
from rag_core.ports import RateInfo
from rag_core.services.quota import Quota

ROOT = Path(__file__).resolve().parents[2]
LIMITS = load_limits(ROOT / "config/limits.yaml")
CONFIG = load_models(ROOT / "tests/fixtures/models.test.yaml")


def test_reported_quota_expires_so_a_per_minute_limit_recovers() -> None:
    ticks = [0.0]
    quota = Quota(CONFIG, LIMITS, monotonic=lambda: ticks[0])
    quota.count("openrouter", RateInfo(remaining=5, limit=100))
    assert quota.over_warn("openrouter")
    ticks[0] += 61
    assert not quota.over_warn("openrouter")  # one call again; its headers say what holds now


def test_counts_locally_without_headers() -> None:
    quota = Quota(CONFIG, LIMITS)
    for _ in range(7):
        quota.count("openrouter", None)
    assert not quota.over_warn("openrouter")
    quota.count("openrouter", None)  # 8 of the fixture's 10 a day
    assert quota.over_warn("openrouter")
