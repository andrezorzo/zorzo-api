"""AC1, AC2, AC3, AC8: the ingest command, run as a subprocess against a fresh migrated database."""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from rag_core.ports import EMBEDDING_DIM

from tests.conftest import ROOT, DomainFactory, create_migrated_database, drop_database, run_ingest

FIXTURE_CORPUS = ROOT / "tests" / "fixtures" / "corpus"
EXAMPLES = ROOT / "domains" / "zorzo" / "src" / "domain_zorzo" / "examples.yaml"
FAKE_TAG = "fake-hash-v1"
DOCUMENTED_SIGTERM_EXIT = 143  # the SIGTERM exit code apps/ingest/AGENTS.md documents


@dataclass
class Run:
    code: int
    output: str
    summary: dict[str, Any]


@pytest.fixture
def db() -> Iterator[str]:
    url = create_migrated_database()
    yield url
    drop_database(url)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    folder = tmp_path / "corpus"
    shutil.copytree(FIXTURE_CORPUS, folder)
    return folder


def ingest(db: str, data_dir: Path, *args: str, app_env: str = "local", embedder: str = "fake") -> Run:
    result = run_ingest(db, data_dir, *args, app_env=app_env, embedder=embedder)
    lines = [line for line in result.stdout.splitlines() if line.startswith('{"summary"')]
    summary = json.loads(lines[-1])["summary"] if lines else {}
    return Run(result.returncode, result.stdout + result.stderr, summary)


def rows(db: str, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def status(db: str, version: int) -> str:
    return str(rows(db, "SELECT status FROM corpus_versions WHERE version = %s", version)[0][0])


def write_doc(folder: Path, name: str, body: str, reviewed: bool = True) -> None:
    (folder / name).write_text(
        "---\n"
        f"doc_key: {name.removesuffix('.md')}\n"
        "title: Extra\nkind: post\nlang: en\nauthority: 1\n"
        f"reviewed_by_owner: {'true' if reviewed else 'false'}\n"
        "---\n# Extra\n\n" + body + "\n"
    )


def assert_rejected(db: str, run: Run, filename: str) -> None:
    assert run.code == 1, run.output
    assert filename in run.output, run.output
    assert run.summary["status"] == "rejected", run.output
    version = run.summary["version"]
    assert status(db, version) == "rejected"
    assert rows(db, "SELECT count(*) FROM documents WHERE corpus_version = %s", version)[0][0] == 0
    assert rows(db, "SELECT count(*) FROM chunks WHERE corpus_version = %s", version)[0][0] == 0


def test_rejects_an_unreviewed_document(db: str, source: Path, tmp_path: Path) -> None:
    write_doc(source, "draft.md", "Not reviewed yet.", reviewed=False)
    assert_rejected(db, ingest(db, tmp_path / "data", "--source", str(source)), "draft.md")


def test_rejects_a_document_with_a_secret(db: str, source: Path, tmp_path: Path) -> None:
    # A made-up key, never a real one, built in two parts. Fixed, with digits: gitleaks' generic rule skips
    # a key with none, which a random one sometimes was.
    key = "sk-proj-" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ2bD4fH6jL8nP0r"
    write_doc(source, "leak.md", f'config: openai_api_key = "{key}"')
    run = ingest(db, tmp_path / "data", "--source", str(source))
    assert_rejected(db, run, "leak.md")
    assert key not in run.output


def test_rejects_a_document_with_personal_data(db: str, source: Path, tmp_path: Path) -> None:
    write_doc(source, "contact.md", "Reach Maria at maria.silva@example.org or +55 11 98765-4321.")
    run = ingest(db, tmp_path / "data", "--source", str(source))
    assert_rejected(db, run, "contact.md")
    assert "maria.silva@example.org" not in run.output
    assert "98765-4321" not in run.output


def test_rejects_personal_data_in_the_front_matter(db: str, source: Path, tmp_path: Path) -> None:
    (source / "titled.md").write_text(
        "---\ndoc_key: titled\ntitle: Contact joao.silva@gmail.com\nkind: post\nlang: en\nauthority: 1\n"
        "reviewed_by_owner: true\n---\n# Clean\n\nNothing here.\n"
    )
    assert_rejected(db, ingest(db, tmp_path / "data", "--source", str(source)), "titled.md")


def test_rejects_a_repeated_doc_key(db: str, source: Path, tmp_path: Path) -> None:
    (source / "faq-copy.md").write_text((source / "faq-en.md").read_text())
    run = ingest(db, tmp_path / "data", "--source", str(source))
    # Files are read in name order, so the second one with the key is the one reported.
    assert_rejected(db, run, "faq-en.md")
    assert "duplicate doc_key" in run.output


def test_rejects_the_candidate_when_embedding_fails(db: str, source: Path, tmp_path: Path) -> None:
    run = ingest(db, tmp_path / "data", "--source", str(source), embedder="ollama")
    assert run.code == 1
    versions = rows(db, "SELECT status FROM corpus_versions")
    assert versions == [("rejected",)]
    assert run.summary == {}  # only a finished run or a gate rejection prints a summary


def test_refuses_to_activate_a_version_that_is_not_a_candidate(db: str, source: Path, tmp_path: Path) -> None:
    active = ingest(db, tmp_path / "data", "--source", str(source), "--activate")
    again = ingest(db, tmp_path / "data", "--activate-version", str(active.summary["version"]))
    assert again.code == 1
    assert "activation_refused" in again.output
    assert again.summary == {}  # a refused activation prints no summary
    missing = ingest(db, tmp_path / "data", "--activate-version", "9999")
    assert missing.code == 1 and missing.summary == {}


def test_refuses_to_activate_an_empty_candidate(db: str, tmp_path: Path) -> None:
    version = rows(
        db, "INSERT INTO corpus_versions (embed_model_tag) VALUES ('fake-hash-v1') RETURNING version"
    )
    run = ingest(db, tmp_path / "data", "--activate-version", str(version[0][0]))
    assert run.code == 1
    assert status(db, version[0][0]) == "candidate"


def test_rejects_a_symlinked_document(db: str, source: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text((source / "faq-en.md").read_text().replace("faq-en", "linked"))
    (source / "linked.md").symlink_to(outside)
    assert_rejected(db, ingest(db, tmp_path / "data", "--source", str(source)), "linked.md")


def test_a_failed_publish_leaves_the_version_active(db: str, source: Path, tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.write_text("")  # a file where the data folder should be: publishing fails, even as root
    run = ingest(db, data, "--source", str(source), "--activate")
    assert run.code == 1
    assert "publish_failed" in run.output
    assert run.summary == {}
    assert rows(db, "SELECT status FROM corpus_versions") == [("active",)]


def test_lets_the_owners_listed_contact_through(db: str, source: Path, tmp_path: Path) -> None:
    write_doc(source, "contact.md", "Write to andre_zorzo@yahoo.com.")
    run = ingest(db, tmp_path / "data", "--source", str(source))
    assert run.code == 0, run.output
    assert run.summary["status"] == "candidate"


def test_splits_by_headers_and_stores_embedded_chunks(db: str, source: Path, tmp_path: Path) -> None:
    run = ingest(db, tmp_path / "data", "--source", str(source))
    assert run.code == 0, run.output
    version = run.summary["version"]
    assert rows(db, "SELECT embed_model_tag FROM corpus_versions WHERE version = %s", version) == [
        (FAKE_TAG,)
    ]
    assert rows(db, "SELECT count(*) FROM documents WHERE corpus_version = %s", version)[0][0] == 3

    chunks = rows(
        db,
        "SELECT heading_path, tokens, vector_dims(embedding), length(tsv), hash FROM chunks "
        "WHERE corpus_version = %s ORDER BY heading_path",
        version,
    )
    paths = [c[0] for c in chunks]
    assert "FAQ > Cloud" in paths
    assert "Narrativa de carreira > Sistemas distribuídos" in paths
    assert all(c[1] > 0 and c[2] == EMBEDDING_DIM and c[3] > 0 for c in chunks)
    assert len({c[4] for c in chunks}) == len(chunks)
    assert run.summary["chunks"] == len(chunks)
    assert run.summary["embedded"] == len(chunks) and run.summary["copied"] == 0


def test_copies_unchanged_chunks_into_the_new_version(db: str, source: Path, tmp_path: Path) -> None:
    first = ingest(db, tmp_path / "data", "--source", str(source))
    faq = source / "faq-en.md"
    faq.write_text(faq.read_text().replace("twenty years", "two decades"))
    second = ingest(db, tmp_path / "data", "--source", str(source))
    assert second.code == 0, second.output

    changed = rows(
        db,
        "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE c.corpus_version = %s AND d.doc_key = 'faq-en' AND c.heading_path = 'FAQ > Experience'",
        second.summary["version"],
    )[0][0]
    assert second.summary["embedded"] == changed
    assert second.summary["copied"] == second.summary["chunks"] - changed
    same = rows(
        db,
        "SELECT count(*) FROM chunks a JOIN chunks b ON a.hash = b.hash "
        "WHERE a.corpus_version = %s AND b.corpus_version = %s AND a.embedding = b.embedding",
        first.summary["version"],
        second.summary["version"],
    )[0][0]
    assert same == second.summary["copied"]


def test_activates_without_an_eval_run_only_when_local(db: str, source: Path, tmp_path: Path) -> None:
    candidate = ingest(db, tmp_path / "data", "--source", str(source))
    version = candidate.summary["version"]

    refused = ingest(db, tmp_path / "data", "--activate-version", str(version), app_env="production")
    assert refused.code == 1
    assert "eval run" in refused.output
    assert status(db, version) == "candidate"

    accepted = ingest(db, tmp_path / "data", "--activate-version", str(version))
    assert accepted.code == 0, accepted.output
    assert status(db, version) == "active"


def test_activation_retires_the_previous_version(db: str, source: Path, tmp_path: Path) -> None:
    first = ingest(db, tmp_path / "data", "--source", str(source), "--activate")
    second = ingest(db, tmp_path / "data", "--source", str(source), "--activate")
    assert second.code == 0, second.output
    assert status(db, first.summary["version"]) == "retired"
    assert status(db, second.summary["version"]) == "active"


def test_publishes_corpus_and_example_data_on_activation(db: str, source: Path, tmp_path: Path) -> None:
    data = tmp_path / "data"
    run = ingest(db, data, "--source", str(source), "--activate")
    assert run.code == 0, run.output
    version = run.summary["version"]

    manifest = json.loads((data / "manifest.json").read_text())
    assert manifest["corpus_version"] == version
    assert manifest["embed_model_tag"] == FAKE_TAG
    folder = data / manifest["path"].removeprefix("data/")
    assert folder.name in manifest["path"] and len(folder.name) == 64

    corpus = json.loads((folder / "corpus.json").read_text())
    db_ids = {r[0] for r in rows(db, "SELECT id FROM chunks WHERE corpus_version = %s", version)}
    assert corpus["corpus_version"] == version
    assert {c["id"] for c in corpus["chunks"]} == db_ids
    assert all(len(c["p"]) == 3 and all(-1 <= x <= 1 for x in c["p"]) for c in corpus["chunks"])
    assert all(c["text"] and c["title"] and c["heading"] for c in corpus["chunks"])

    examples = json.loads((folder / "examples.json").read_text())
    expected = [
        {"id": e["id"], "lang": e["lang"], "text": e["text"]} for e in yaml.safe_load(EXAMPLES.read_text())
    ]
    assert examples == expected


def test_sigterm_rejects_the_candidate(db: str, source: Path, tmp_path: Path) -> None:
    # An "Ollama" that accepts the connection and never answers: the run waits inside embedding.
    with socket.create_server(("127.0.0.1", 0)) as server:
        server.settimeout(60)
        port = server.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "rag_ingest", "--source", str(source)],
            cwd=ROOT,
            env={
                **os.environ,
                "DATABASE_URL": db,
                "APP_ENV": "local",
                "DOMAIN": "zorzo",
                "EMBEDDER": "ollama",
                "OLLAMA_URL": f"http://127.0.0.1:{port}",
                "DATA_DIR": str(tmp_path / "data"),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            connection, _ = server.accept()  # embedding has started
            with connection:
                process.send_signal(signal.SIGTERM)
                output, _ = process.communicate(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
                print(process.communicate()[0])
    assert process.returncode == DOCUMENTED_SIGTERM_EXIT, output
    assert "ingest_interrupted" in output
    assert rows(db, "SELECT status FROM corpus_versions") == [("rejected",)]


def test_refuses_a_text_search_configuration_postgres_lacks(
    db: str, tmp_path: Path, make_domain: DomainFactory
) -> None:
    # F01-T14 AC3: checked before the run writes anything.
    name = make_domain(
        "klingon",
        {"domain.yaml": "name: klingon\nlanguages: {en: english, es: klingon}\ndocument_kinds: [manual]\n"},
        from_sample=True,
    )
    result = run_ingest(db, tmp_path / "data", domain=name, domains_dir=make_domain.folder)
    assert result.returncode == 1
    assert "klingon" in result.stderr
    assert rows(db, "SELECT version FROM corpus_versions") == []
