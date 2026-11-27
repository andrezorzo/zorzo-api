"""F01-T14 AC5: another domain, with other document kinds and another language, ingests and answers through
the same core, with no change outside its own package (`tests/fixtures/domains/domain_sample`)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.api.test_ask import QUESTION, ask
from tests.conftest import (
    FIXTURE_DOMAINS,
    api_settings,
    build_app,
    create_migrated_database,
    drop_database,
    run_ingest,
    seed_budget,
)


@pytest.fixture
def sample_database(tmp_path: Path) -> Iterator[str]:
    url = create_migrated_database()
    result = run_ingest(url, tmp_path / "data", "--activate", domain="sample", domains_dir=FIXTURE_DOMAINS)
    assert result.returncode == 0, result.stdout + result.stderr
    yield url
    drop_database(url)


def test_another_domain_ingests_and_answers_search_only(
    sample_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(FIXTURE_DOMAINS))
    seed_budget(sample_database, 0.10, paid_disabled=True, degraded=True)  # search-only: no model call
    with TestClient(build_app(api_settings(sample_database, domain="sample"))) as client:
        stream = ask(client, {**QUESTION, "q": "¿Cómo se cuida un árbol?"})
    assert stream.only("degraded")[0].data["mode"] == "search_only"
    hits = stream.only("retrieval")[0].data["hits"]
    # "árbol" matches "árboles" only through the domain's Spanish configuration.
    assert hits[0]["title"] == "Manual del parque"
