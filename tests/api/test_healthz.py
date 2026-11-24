"""AC1: the health endpoint reports the database and the budget mode."""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from zorzo_api.main import create_app

from tests.conftest import api_settings, create_migrated_database, drop_database


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    with TestClient(create_app(api_settings(database_url))) as c:
        yield c


def test_reports_ok_with_the_database_up(client: TestClient, database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        conn.execute("DELETE FROM budget_daily")
    response = client.get("/api/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "normal", "db": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_reports_degraded_when_todays_budget_is_exhausted(client: TestClient, database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "INSERT INTO budget_daily (day, degraded_at) VALUES ((now() AT TIME ZONE 'UTC')::date, now()) "
            "ON CONFLICT (day) DO UPDATE SET degraded_at = now()"
        )
    try:
        response = client.get("/api/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "mode": "degraded", "db": "ok"}
    finally:
        with psycopg.connect(database_url) as conn:
            conn.execute("DELETE FROM budget_daily")


def test_reports_down_when_the_database_is_unreachable() -> None:
    url = create_migrated_database()
    with TestClient(create_app(api_settings(url))) as client:
        drop_database(url)
        response = client.get("/api/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "down", "mode": "unknown", "db": "down"}
    assert response.headers["cache-control"] == "no-store"
