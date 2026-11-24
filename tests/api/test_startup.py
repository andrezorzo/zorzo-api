"""AC2 and AC4: the API loads the domain chosen by DOMAIN and refuses to start on bad settings."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from zorzo_api.main import create_app
from zorzo_core.domain import DomainError

from tests.conftest import api_settings


def _start(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DOMAIN", "APP_ENV")}
    return subprocess.run(
        [sys.executable, "-m", "zorzo_api"],
        env={**base, "PORT": "0", **env},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_loads_the_domain_chosen_by_domain(database_url: str) -> None:
    app = create_app(api_settings(database_url))  # type: ignore[arg-type]
    assert app.state.domain.name == "zorzo"
    assert app.state.domain.languages == ("en", "pt")


def test_refuses_a_missing_domain(database_url: str) -> None:
    with pytest.raises(DomainError, match="domain_missing"):
        create_app(api_settings(database_url, domain="missing"))  # type: ignore[arg-type]
    result = _start({"DATABASE_URL": database_url, "DOMAIN": "missing"})
    assert result.returncode != 0
    assert "domain_missing" in result.stderr


def test_stops_without_database_url() -> None:
    result = _start({"DOMAIN": "zorzo"})
    assert result.returncode != 0
    assert "database_url" in result.stderr.lower()


def test_stops_on_an_invalid_domain_config(database_url: str, tmp_path: Path) -> None:
    package = tmp_path / "domain_broken"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "domain.yaml").write_text("name: broken\nunexpected: field\n")
    result = _start({"DATABASE_URL": database_url, "DOMAIN": "broken", "PYTHONPATH": str(tmp_path)})
    assert result.returncode != 0
    assert "invalid domain config" in result.stderr
