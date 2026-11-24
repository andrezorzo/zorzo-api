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


def api_settings(url: str, domain: str = "zorzo") -> ApiSettings:
    """Settings for an API under test, pointing at the given database."""
    return ApiSettings(app_env="local", domain=domain, database_url=url, log_level="INFO")
