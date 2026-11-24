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


async def _main(args: argparse.Namespace, settings: IngestSettings, config: _Config) -> int:
    domain, examples, limits = config.domain, config.examples, config.limits
    db = Database(settings.database_url)
    store = PgCorpusStore(db)
    publisher = LocalDataPublisher(settings.data_dir)
    ingested: Summary | None = None
    try:
        async with store.lock() as got:
            if not got:
                log.error("ingest_busy", reason="another ingest run holds the lock")
                return 1
            if args.activate_version is not None:
                version = args.activate_version
                await _activate_and_publish(version, store, publisher, settings, examples)
                _summary(Summary(version, "active"))
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
            ingested = summary
            if args.activate:
                await _activate_and_publish(summary.version, store, publisher, settings, examples)
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
        if ingested is not None:
            _summary(ingested)  # the candidate was written: callers still learn its version
        return 1
    except (ConfigError, DomainError):
        raise  # reported by main() as a refusal to start
    except Exception:
        # A failed ingest run has rejected its candidate. A failure after activation leaves the version
        # active, possibly without published data: re-running the ingest publishes a new version.
        log.exception("ingest_failed")
        if ingested is not None:
            # The real status: the failure may have come after the version went live. The lookup itself can
            # fail (the database may be why the run stopped), and the summary line must still be printed.
            try:
                status = await store.status(ingested.version)
            except Exception:
                log.warning("status_lookup_failed", version=ingested.version, exc_info=True)
                status = None
            _summary(replace(ingested, status=status or "unknown"))
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
    except (ConfigError, DomainError) as exc:  # e.g. EMBEDDER=fake outside APP_ENV=local
        print(f"rag-ingest: refusing to start: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
