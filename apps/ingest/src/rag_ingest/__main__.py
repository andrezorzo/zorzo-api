"""The ingest command (LLD "6.1 Corpus version").

    python -m rag_ingest [--source DIR] [--activate]   write a new candidate version, optionally activate it
    python -m rag_ingest --activate-version N          activate an existing candidate

Prints one JSON summary line (`{"summary": {...}}`) and exits 1 on a rejected run, a refused activation
or a busy lock.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import structlog
from pydantic import ValidationError
from rag_core.adapters.factory import ConfigError, build_embedder
from rag_core.adapters.gitleaks import GitleaksScanner
from rag_core.adapters.local_publisher import LocalDataPublisher
from rag_core.adapters.pgvector_store import PgCorpusStore
from rag_core.adapters.postgres import Database
from rag_core.domain import DomainError, corpus_dir, load_domain, load_examples
from rag_core.ingest import publish
from rag_core.ingest.pipeline import ActivationRefused, IngestRejected, Summary, activate, run_ingest
from rag_core.limits import load_limits
from rag_core.ports import DataPublisher
from rag_core.telemetry import configure_logging

from rag_ingest.settings import IngestSettings

log = structlog.get_logger()


def _summary(summary: Summary) -> None:
    print(json.dumps({"summary": asdict(summary)}), flush=True)


async def _activate_and_publish(
    version: int, store: PgCorpusStore, publisher: DataPublisher, settings: IngestSettings, tag: str
) -> None:
    # Local development may activate without an eval run; production never does (LLD "6.1 Corpus version").
    await activate(version, store=store, require_eval=settings.app_env != "local")
    log.info("corpus_activated", version=version)
    folder, files, manifest = publish.build(
        version, tag, await store.published_chunks(version), load_examples(settings.domain)
    )
    await publisher.publish(folder, files, manifest)
    log.info("corpus_published", version=version, path=folder)


async def _main(args: argparse.Namespace, settings: IngestSettings) -> int:
    domain = load_domain(settings.domain)
    db = Database(settings.database_url)
    store = PgCorpusStore(db)
    publisher = LocalDataPublisher(settings.data_dir)
    try:
        async with store.lock() as got:
            if not got:
                log.error("ingest_busy", reason="another ingest run holds the lock")
                return 1
            if args.activate_version is not None:
                version = args.activate_version
                tag = await store.embed_model_tag(version)
                if tag is None:
                    raise ActivationRefused(f"corpus version {version} does not exist")
                await _activate_and_publish(version, store, publisher, settings, tag)
                _summary(Summary(version, "active"))
                return 0
            embedder = build_embedder(settings)
            summary = await run_ingest(
                args.source or corpus_dir(settings.domain),
                store=store,
                embedder=embedder,
                scanner=GitleaksScanner(settings.gitleaks_binary),
                allowlist=domain.pii_allowlist,
                limits=load_limits(settings.limits_file),
            )
            log.info("corpus_ingested", **asdict(summary))
            if args.activate:
                await _activate_and_publish(summary.version, store, publisher, settings, embedder.tag)
                summary = replace(summary, status="active")
            _summary(summary)
            return 0
    except IngestRejected as exc:
        for problem in exc.problems:
            log.error("ingest_rejected", file=problem.file, rule=problem.rule)
        _summary(exc.summary)
        return 1
    except ActivationRefused as exc:
        log.error("activation_refused", reason=str(exc))
        return 1
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="rag_ingest", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", type=Path, help="folder of Markdown documents (default: the domain corpus)"
    )
    parser.add_argument(
        "--activate", action="store_true", help="activate the new version and publish its data"
    )
    parser.add_argument("--activate-version", type=int, metavar="N", help="activate an existing candidate")
    args = parser.parse_args()
    try:
        settings = IngestSettings()  # type: ignore[call-arg]
        configure_logging(settings.app_env, settings.log_level)
        sys.exit(asyncio.run(_main(args, settings)))
    except (ValidationError, DomainError, ConfigError) as exc:
        print(f"rag-ingest: refusing to start: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
