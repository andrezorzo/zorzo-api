"""Shared fixtures: one test database per session, migrated by Django."""

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from rag_api.settings import ApiSettings
from sqlalchemy import make_url

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://zorzo:zorzo@localhost:5432/postgres")


def _url_for(name: str) -> str:
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


def create_migrated_database() -> str:
    """Create a fresh database, apply the Django migrations and return its URL."""
    name = f"zorzo_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = _url_for(name)
    subprocess.run(
        [sys.executable, str(ROOT / "apps/dashboard/manage.py"), "migrate", "--noinput"],
        check=True,
        env={**os.environ, "DATABASE_URL": url, "APP_ENV": "local"},
        capture_output=True,
    )
    return url


def drop_database(url: str) -> None:
    name = make_url(url).database
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = create_migrated_database()
    yield url
    drop_database(url)


def ingest_fixture_corpus(url: str, data_dir: Path) -> None:
    """Ingest and activate the fixture corpus with the real command and the fake embedder."""
    subprocess.run(
        [sys.executable, "-m", "rag_ingest", "--source", str(ROOT / "tests/fixtures/corpus"), "--activate"],
        check=True,
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": url,
            "APP_ENV": "local",
            "DOMAIN": "zorzo",
            "EMBEDDER": "fake",
            "DATA_DIR": str(data_dir),
        },
        capture_output=True,
    )


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
    }
    return ApiSettings(**{**values, **overrides})  # type: ignore[arg-type]
