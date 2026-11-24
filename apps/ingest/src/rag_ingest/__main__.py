"""The ingest command (LLD "6.1 Corpus version").

    python -m rag_ingest [--source DIR] [--activate]   write a new candidate version, optionally activate it
    python -m rag_ingest --activate-version N          activate an existing candidate

Prints one JSON summary line (`{"summary": {...}}`) whenever it created or acted on a version, with the
version's real status (`unknown` if the database can't be read), and exits 1 on a rejected or failed run,
a refused activation or a busy lock.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import structlog
import yaml
from pydantic import ValidationError
from rag_core.adapters.factory import ConfigError, build_embedder
from rag_core.adapters.gitleaks import GitleaksScanner
from rag_core.adapters.local_publisher import LocalDataPublisher
from rag_core.adapters.pgvector_store import PgCorpusStore
from rag_core.adapters.postgres import Database
from rag_core.domain import DomainConfig, DomainError, Example, corpus_dir, load_domain, load_examples
from rag_core.ingest.pipeline import (
    ActivationRefused,
    IngestFailed,
    IngestRejected,
    Summary,
    activate_and_publish,
    run_ingest,
)
from rag_core.limits import Limits, load_limits
from rag_core.ports import DataPublisher
from rag_core.telemetry import configure_logging

from rag_ingest.settings import IngestSettings

log = structlog.get_logger()


def _summary(summary: Summary) -> None:
    print(json.dumps({"summary": asdict(summary)}), flush=True)


async def _activate_and_publish(
    version: int,
    store: PgCorpusStore,
    publisher: DataPublisher,
    settings: IngestSettings,
    examples: list[Example],
) -> None:
    folder = await activate_and_publish(
        version,
        store=store,
        publisher=publisher,
        examples=examples,
        # Local may activate without an eval run; production never does (LLD "6.1 Corpus version").
        require_eval=settings.app_env != "local",
    )
    log.info("corpus_activated_and_published", version=version, path=folder)


@dataclass(frozen=True)
class _Config:
    """Everything read from files, before the run touches the database."""

    domain: DomainConfig
    examples: list[Example]
    limits: Limits


async def _report_failure(store: PgCorpusStore, version: int | None) -> None:
    """The summary of a version a failed run created or touched, with its real status from the database.

    The failure may have come before or after the version went live, or the reject may have failed, so the
    status is read, never assumed; `unknown` when the database can't be read, nothing when there's no such
    version.
    """
    if version is None:
        return
    try:
        status = await store.status(version)
    except Exception:
        log.warning("status_lookup_failed", version=version, exc_info=True)
        status = "unknown"
    if status is not None:
        _summary(Summary(version, status))


async def _main(args: argparse.Namespace, settings: IngestSettings, config: _Config) -> int:
    domain, examples, limits = config.domain, config.examples, config.limits
    db = Database(settings.database_url)
    store = PgCorpusStore(db)
    publisher = LocalDataPublisher(settings.data_dir)
    touched: int | None = None  # the version this run created or acted on, for the failure summary
    try:
        async with store.lock() as got:
            if not got:
                log.error("ingest_busy", reason="another ingest run holds the lock")
                return 1
            if args.activate_version is not None:
                touched = args.activate_version
                await _activate_and_publish(touched, store, publisher, settings, examples)
                _summary(Summary(touched, "active"))
                return 0
            embedder = build_embedder(settings)
            summary = await run_ingest(
                args.source or corpus_dir(settings.domain),
                store=store,
                embedder=embedder,
                scanner=GitleaksScanner(settings.gitleaks_binary),
                allowlist=domain.pii_allowlist,
                limits=limits,
            )
            log.info("corpus_ingested", **asdict(summary))
            touched = summary.version
            if args.activate:
                await _activate_and_publish(summary.version, store, publisher, settings, examples)
                summary = replace(summary, status="active")
            _summary(summary)
            return 0
    except IngestFailed as exc:
        log.exception("ingest_failed")
        await _report_failure(store, exc.summary.version)
        return 1
    except IngestRejected as exc:
        for problem in exc.problems:
            log.error("ingest_rejected", file=problem.file, rule=problem.rule)
        await _report_failure(store, exc.summary.version)
        return 1
    except ActivationRefused as exc:
        log.error("activation_refused", reason=str(exc))
        await _report_failure(store, touched)
        return 1
    except ConfigError:
        raise  # reported by main()
    except Exception:
        # A failure after activation leaves the version active, possibly without published data:
        # re-running the ingest publishes a new version.
        log.exception("ingest_failed")
        await _report_failure(store, touched)
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
    if args.activate_version is not None and (args.activate or args.source):
        parser.error(
            "--activate-version activates an existing candidate; it can't be combined with an ingest"
        )
    try:
        settings = IngestSettings()  # type: ignore[call-arg]
        configure_logging(settings.app_env, settings.log_level)
        # Read before any write: a bad file stops the command before it changes anything.
        config = _Config(
            load_domain(settings.domain), load_examples(settings.domain), load_limits(settings.limits_file)
        )
    except (ValidationError, DomainError, yaml.YAMLError, OSError) as exc:
        print(f"rag-ingest: refusing to start: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        sys.exit(asyncio.run(_main(args, settings, config)))
    except ConfigError as exc:  # e.g. EMBEDDER=fake outside APP_ENV=local
        print(f"rag-ingest: refusing to run: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
