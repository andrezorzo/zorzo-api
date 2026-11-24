"""AC2 and AC4: the API loads the domain chosen by DOMAIN and refuses to start on bad settings."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from rag_api.main import create_app
from rag_core.domain import DomainError

from tests.conftest import api_settings


def _start(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {
        k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DOMAIN", "APP_ENV", "LOG_LEVEL")
    }
    return subprocess.run(
        [sys.executable, "-m", "rag_api"],
        env={**base, "PORT": "0", **env},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_loads_the_domain_chosen_by_domain(database_url: str) -> None:
    app = create_app(api_settings(database_url))
    assert app.state.domain.name == "zorzo"
    assert app.state.domain.languages == ("en", "pt")


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
