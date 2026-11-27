"""Shared fixtures: test databases migrated by Django.

The migrations run once per test session into a template database; each test database is a copy of it
(`CREATE DATABASE … TEMPLATE`), which takes milliseconds instead of a Django run.
"""

import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from rag_api.main import create_app
from rag_api.settings import ApiSettings
from rag_core.adapters.factory import build_gateway, rerank_chain
from rag_core.adapters.turnstile import TurnstileBotCheck
from rag_core.limits import Limits
from rag_core.models_config import ModelsConfig
from rag_core.services.gateway import ModelGateway
from sqlalchemy import make_url

from tests.fakes.alerter import RecordingAlerter
from tests.fakes.openrouter import FakeOpenRouter, stop_all
from tests.fakes.turnstile import PASS_SECRET, FakeSiteverify

ROOT = Path(__file__).resolve().parents[1]
# Test-only domain packages (`domain_<name>`), importable once this folder is on the path.
FIXTURE_DOMAINS = ROOT / "tests/fixtures/domains"
ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://zorzo:zorzo@localhost:5432/postgres")


def _url_for(name: str) -> str:
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


_template: str | None = None


def _migrated_template() -> str:
    """The session's template database, created and migrated on first use."""
    global _template
    if _template is None:
        name = f"zorzo_tmpl_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        subprocess.run(
            [sys.executable, str(ROOT / "apps/dashboard/manage.py"), "migrate", "--noinput"],
            check=True,
            env={**os.environ, "DATABASE_URL": _url_for(name), "APP_ENV": "local"},
            capture_output=True,
        )
        _template = name
    return _template


def pytest_sessionfinish() -> None:
    if _template is not None:
        drop_database(_url_for(_template))


def create_migrated_database() -> str:
    """A fresh database with the Django migrations applied; returns its URL."""
    name = f"zorzo_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{_migrated_template()}"')
    return _url_for(name)


def drop_database(url: str) -> None:
    name = make_url(url).database
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = create_migrated_database()
    yield url
    drop_database(url)


def run_ingest(
    url: str,
    data_dir: Path,
    *args: str,
    app_env: str = "local",
    embedder: str = "fake",
    domain: str = "zorzo",
    domains_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """The real ingest command (fake embedder by default) against the given database; `domains_dir` puts a
    folder of test domain packages on the path."""
    path = {"PYTHONPATH": str(domains_dir)} if domains_dir else {}
    return subprocess.run(
        [sys.executable, "-m", "rag_ingest", *args],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": url,
            "APP_ENV": app_env,
            "DOMAIN": domain,
            "EMBEDDER": embedder,
            # Nothing listens here: with EMBEDDER=ollama, embedding fails at once.
            "OLLAMA_URL": "http://127.0.0.1:9",
            "DATA_DIR": str(data_dir),
            **path,
        },
        capture_output=True,
        text=True,
        timeout=120,
    )


class DomainFactory:
    """Builds throwaway `domain_<name>` packages in `folder`, which is on the import path for the test (and
    can be passed as `PYTHONPATH` to a subprocess); each package's import is dropped after the test."""

    def __init__(self, folder: Path, request: pytest.FixtureRequest) -> None:
        self.folder = folder
        self._request = request

    def __call__(
        self, name: str, texts: Mapping[str, str] | None = None, *, from_sample: bool = False
    ) -> str:
        """An empty package, or a copy of the `sample` test domain, with the given files (`__` in a key
        stands for `/`, e.g. `prompts__route.md`); returns the domain name."""
        package = self.folder / f"domain_{name}"  # one package per name: imports are cached by name
        if from_sample:
            shutil.copytree(FIXTURE_DOMAINS / "domain_sample", package)
        else:
            package.mkdir()
            (package / "__init__.py").write_text("")
        for relative, text in (texts or {}).items():
            path = package / relative.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self._request.addfinalizer(lambda: sys.modules.pop(f"domain_{name}", None))
        return name


@pytest.fixture
def make_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> DomainFactory:
    folder = tmp_path / "domains"
    folder.mkdir()
    monkeypatch.syspath_prepend(str(folder))
    return DomainFactory(folder, request)


CORE = ROOT / "packages/core/src/rag_core"


def named_in_core(names: set[str]) -> dict[str, list[str]]:
    """The given names written as a quoted string in a core source file, by file."""
    found: dict[str, list[str]] = {}
    for path in CORE.rglob("*.py"):
        text = path.read_text()
        hits = sorted(n for n in names if re.search(rf"[\"']{re.escape(n)}[\"']", text))
        if hits:
            found[str(path.relative_to(CORE))] = hits
    return found


def ingest_fixture_corpus(url: str, data_dir: Path) -> None:
    """Ingest and activate the fixture corpus (spec, seam 1)."""
    result = run_ingest(url, data_dir, "--source", str(ROOT / "tests/fixtures/corpus"), "--activate")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="session")
def corpus_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A database with the fixture corpus ingested and active (spec, seam 1)."""
    url = create_migrated_database()
    ingest_fixture_corpus(url, tmp_path_factory.mktemp("data"))
    yield url
    drop_database(url)


def api_settings(url: str, domain: str = "zorzo", **overrides: object) -> ApiSettings:
    """Settings for an API under test, pointing at the given database; fakes unless overridden."""
    values: dict[str, object] = {
        "app_env": "local",
        "domain": domain,
        "database_url": url,
        "log_level": "INFO",
        "embedder": "fake",
        "reranker": "fake",
        "turnstile_secret": PASS_SECRET,
        "salt_seed": "test-seed-" * 4,
        "models_file": ROOT / "tests/fixtures/models.test.yaml",
        "openrouter_api_key": "test-key",
    }
    return ApiSettings(**{**values, **overrides})  # type: ignore[arg-type]


# Most tests ask many questions from one client: limits high enough that they never apply. The gate tests
# pass their own, small ones.
ROOMY_LIMITS = {
    "rate_ip_burst": 10_000,
    "rate_ip_daily": 10_000,
    "rate_session_burst": 10_000,
    "rate_session_daily": 10_000,
}


def build_app(
    settings: ApiSettings,
    siteverify: FakeSiteverify | None = None,
    limits: dict[str, object] | None = None,
    openrouter: FakeOpenRouter | None = None,
) -> FastAPI:
    """The real app, with Turnstile's `siteverify` and OpenRouter replaced by stand-ins, alerts recorded
    on `app.state.alerter` and the given limits."""
    app = create_app(settings)
    gate = app.state.gate
    # Validated like the file, so a test never runs with values the app would refuse at startup.
    gate.limits = app.state.limits = Limits.model_validate(
        {**app.state.limits.model_dump(), **ROOMY_LIMITS, **(limits or {})}
    )
    gate.bot_check = TurnstileBotCheck(
        settings.turnstile_secret.get_secret_value(),
        gate.limits.timeout_turnstile,
        transport=(siteverify or FakeSiteverify()).transport(),
    )
    # The gateway again, wired as the app wires it, with the stand-in's transport and the test limits.
    old: ModelGateway = app.state.gateway
    app.state.openrouter = fake = openrouter or FakeOpenRouter()
    app.state.alerter = RecordingAlerter()
    config = served(old.config, fake)
    app.state.gateway = build_gateway(
        config, {"openrouter": "test-key"}, app.state.db, app.state.alerter, gate.limits
    )
    app.state.rerankers = rerank_chain(app.state.gateway, app.state.local_rerankers)
    return app


def served(config: ModelsConfig, fake: FakeOpenRouter) -> ModelsConfig:
    """The models config with the OpenRouter provider pointing at the fake, served on a local port."""
    provider = config.providers["openrouter"].model_copy(update={"base_url": fake.serve()})
    return config.model_copy(update={"providers": {**config.providers, "openrouter": provider}})


@pytest.fixture(autouse=True)
def _stop_fake_servers() -> Iterator[None]:
    yield
    stop_all()


def reset_tables(url: str, *tables: str) -> None:
    with psycopg.connect(url) as conn:
        for table in tables:
            conn.execute(f"DELETE FROM {table}")


def seed_budget(url: str, cost: float, paid_disabled: bool = False, degraded: bool = False) -> None:
    """Today's (UTC) budget row with the given spend and states."""
    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO budget_daily (day, cost_usd, paid_disabled_at, degraded_at) "
            "VALUES ((now() AT TIME ZONE 'UTC')::date, %s, CASE WHEN %s THEN now() END, "
            "CASE WHEN %s THEN now() END) ON CONFLICT (day) DO UPDATE SET cost_usd = EXCLUDED.cost_usd, "
            "paid_disabled_at = EXCLUDED.paid_disabled_at, degraded_at = EXCLUDED.degraded_at",
            (cost, paid_disabled, degraded),
        )
