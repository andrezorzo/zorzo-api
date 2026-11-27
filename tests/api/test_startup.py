"""AC2 and AC4: the API loads the domain chosen by DOMAIN and refuses to start on bad settings."""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from rag_api.main import create_app
from rag_api.settings import PUBLISHED_LOCAL_SEED
from rag_core.adapters.factory import ConfigError
from rag_core.domain import DomainError
from rag_core.models_config import config_version, load_models

from tests.conftest import DomainFactory, api_settings
from tests.fakes.turnstile import PASS_SECRET


def _start(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {
        k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DOMAIN", "APP_ENV", "LOG_LEVEL")
    }
    return subprocess.run(
        [sys.executable, "-m", "rag_api"],
        env={
            **base,
            "PORT": "0",
            "APP_ENV": "local",
            "TURNSTILE_SECRET": PASS_SECRET,
            "SALT_SEED": "test-seed-" * 4,
            **env,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_loads_the_domain_chosen_by_domain(database_url: str) -> None:
    app = create_app(api_settings(database_url))
    assert app.state.domain.name == "zorzo"
    assert tuple(app.state.domain.languages) == ("en", "pt")


def test_refuses_a_missing_domain(database_url: str) -> None:
    with pytest.raises(DomainError, match="domain_missing"):
        create_app(api_settings(database_url, domain="missing"))
    result = _start({"DATABASE_URL": database_url, "DOMAIN": "missing"})
    assert result.returncode != 0
    assert "domain_missing" in result.stderr


def test_stops_without_database_url() -> None:
    result = _start({"DOMAIN": "zorzo"})
    assert result.returncode != 0
    assert "database_url" in result.stderr.lower()


def test_refuses_a_malformed_database_url_without_printing_it() -> None:
    result = _start({"DOMAIN": "zorzo", "DATABASE_URL": "mysql://user:s3cret-pw@db/x"})
    assert result.returncode == 1
    assert "refusing to start" in result.stderr
    assert "s3cret-pw" not in result.stderr


def test_stops_on_an_invalid_domain_config(database_url: str, tmp_path: Path) -> None:
    package = tmp_path / "domain_broken"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "domain.yaml").write_text("name: broken\nunexpected: field\n")
    result = _start({"DATABASE_URL": database_url, "DOMAIN": "broken", "PYTHONPATH": str(tmp_path)})
    assert result.returncode != 0
    assert "invalid domain config" in result.stderr


def test_stops_on_a_text_search_configuration_postgres_lacks(
    database_url: str, make_domain: DomainFactory
) -> None:
    # F01-T14 AC3: the full-text search of that language would fail on every question.
    name = make_domain(
        "klingon",
        {"domain.yaml": "name: klingon\nlanguages: {en: english, es: klingon}\ndocument_kinds: [manual]\n"},
        from_sample=True,
    )
    result = _start({"DATABASE_URL": database_url, "DOMAIN": name, "PYTHONPATH": str(make_domain.folder)})
    assert result.returncode != 0
    assert "klingon" in result.stderr


@pytest.mark.parametrize(
    ("url", "accepted"),
    [
        ("postgresql+psycopg://u:p@db:5432/x", True),
        ("postgresql:///x?host=/var/run/postgresql", True),
        ("postgresql://u:pa#ss@db:5432/x", False),
        ("db:5432/x", False),
    ],
)
def test_validates_the_database_url(url: str, accepted: bool) -> None:
    try:
        api_settings(url)
    except ValidationError:
        assert not accepted
    else:
        assert accepted


def test_refuses_an_unreadable_limits_file(database_url: str, tmp_path: Path) -> None:
    bad = tmp_path / "limits.yaml"
    bad.write_text("ping_interval: [10\n")
    result = _start({"DOMAIN": "zorzo", "DATABASE_URL": database_url, "LIMITS_FILE": str(bad)})
    assert result.returncode == 1
    assert "refusing to start" in result.stderr


def test_refuses_a_turnstile_test_secret_in_production(database_url: str) -> None:
    with pytest.raises(ValidationError, match="test secret"):
        api_settings(database_url, app_env="production")


def test_refuses_the_published_salt_seed_in_production(database_url: str) -> None:
    with pytest.raises(ValidationError, match="SALT_SEED"):
        api_settings(
            database_url,
            app_env="production",
            turnstile_secret="0x4AAAAAAAreal-looking-secret",
            salt_seed="local-salt-seed-change-me-0123456789",
        )


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION: dict[str, Any] = {
    "app_env": "production",
    "turnstile_secret": "0x4AAAAAAAreal-looking-secret",
    "salt_seed": "a-production-seed-that-is-long-enough",
    "telegram_bot_token": "123:abc",
    "telegram_chat_id": "42",
    "models_file": ROOT / "config/models.yaml",
    "embedder": "ollama",
    "reranker": "local",
}


def test_refuses_a_provider_without_its_key_in_production(database_url: str) -> None:
    settings = api_settings(database_url, **{**PRODUCTION, "openrouter_api_key": None})
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        create_app(settings)
    assert create_app(api_settings(database_url, **PRODUCTION)).state.gateway  # with the key, it starts


def test_refuses_production_without_telegram(database_url: str) -> None:
    with pytest.raises(ValidationError, match="TELEGRAM"):
        api_settings(database_url, **{**PRODUCTION, "telegram_chat_id": None})


def test_local_profile_loads_models_local_yaml(database_url: str) -> None:
    app = create_app(api_settings(database_url, models_file=None))
    models = load_models(ROOT / "config/models.local.yaml")
    assert app.state.gateway.config == models
    assert app.state.model_config_version == config_version(models, app.state.limits)


def test_refuses_an_invalid_models_file(database_url: str, tmp_path: Path) -> None:
    bad = tmp_path / "models.yaml"
    bad.write_text("providers: {}\nstages: {}\n")
    result = _start({"DOMAIN": "zorzo", "DATABASE_URL": database_url, "MODELS_FILE": str(bad)})
    assert result.returncode == 1
    assert "refusing to start" in result.stderr


def test_the_guarded_seed_is_the_published_example() -> None:
    root = Path(__file__).resolve().parents[2]
    assert f"SALT_SEED={PUBLISHED_LOCAL_SEED}\n" in (root / "infra/.env.example").read_text()
    # Every seed AGENTS.md gives is the guarded one; a `<placeholder>` is not a seed.
    seeds = re.findall(r"SALT_SEED=([^\s`|<]+)", (root / "apps/api/AGENTS.md").read_text())
    assert seeds and set(seeds) == {PUBLISHED_LOCAL_SEED}
