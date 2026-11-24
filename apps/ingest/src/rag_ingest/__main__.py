"""The ingest command (LLD "6.1 Corpus version").

    python -m rag_ingest [--source DIR] [--activate]   write a new candidate version, optionally activate it
    python -m rag_ingest --activate-version N          activate an existing candidate

Prints one JSON summary line (`{"summary": {...}}`) whenever it created a version or changed one, with the
status the run left it in (`rejected`, `candidate`, `active`, or `unknown` when a write may or may not have
reached the database), and exits 1 on a rejected or failed run, a refused activation, a failed publish or a
busy lock.
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
    PublishFailed,
    Summary,
    activate,
    publish_active,
    run_ingest,
)
from rag_core.limits import Limits, load_limits
from rag_core.telemetry import configure_logging

from rag_ingest.settings import IngestSettings

log = structlog.get_logger()


def _summary(summary: Summary) -> None:
    print(json.dumps({"summary": asdict(summary)}), flush=True)


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
    # What this run knows about the version it created or changed, kept current step by step, so a failure
    # reports it without reading the database (which may be why the run failed).
    known: Summary | None = None

    async def go_live(version: int) -> None:
        nonlocal known
        try:
            # Local may activate without an eval run; production never does (LLD "6.1 Corpus version").
            await activate(version, store=store, require_eval=settings.app_env != "local")
        except ActivationRefused:
            raise
        except Exception:
            if known is not None:
                known = replace(known, status="unknown")  # the activation may or may not have committed
            raise
        known = replace(known, status="active") if known else Summary(version, "active")
        folder = await publish_active(version, store=store, publisher=publisher, examples=examples)
        log.info("corpus_activated_and_published", version=version, path=folder)

    try:
        async with store.lock() as got:
            if not got:
                log.error("ingest_busy", reason="another ingest run holds the lock")
                return 1
            if args.activate_version is not None:
                await go_live(args.activate_version)
            else:
                known = await run_ingest(
                    args.source or corpus_dir(settings.domain),
                    store=store,
                    embedder=build_embedder(settings),
                    scanner=GitleaksScanner(settings.gitleaks_binary),
                    allowlist=domain.pii_allowlist,
                    limits=limits,
                )
                log.info("corpus_ingested", **asdict(known))
                if args.activate:
                    await go_live(known.version)
            if known is not None:  # always set by now; the check keeps the type honest
                _summary(known)
            return 0
    except IngestRejected as exc:
        for problem in exc.problems:
            log.error("ingest_rejected", file=problem.file, rule=problem.rule)
        _summary(exc.summary)
        return 1
    except IngestFailed as exc:
        log.exception("ingest_failed")
        _summary(exc.summary)
        return 1
    except ActivationRefused as exc:
        log.error("activation_refused", reason=str(exc))
        if known is not None:
            _summary(known)  # the candidate this run wrote, still a candidate
        return 1
    except PublishFailed as exc:
        # Live but unpublished: re-running the ingest publishes a new version.
        log.exception("publish_failed", version=exc.version)
        if known is not None:
            _summary(known)
        return 1
    except ConfigError:
        raise  # reported by main()
    except Exception:
        log.exception("ingest_failed")
        if known is not None:
            _summary(known)
        return 1
    except BaseException:  # interrupted: still say what was left behind, then stop
        if known is not None:
            _summary(known)
        raise
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
