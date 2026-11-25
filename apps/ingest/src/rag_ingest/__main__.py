"""The ingest command (LLD "6.1 Corpus version").

    python -m rag_ingest [--source DIR] [--activate]   write a new candidate version, optionally activate it
    python -m rag_ingest --activate-version N          activate an existing candidate

Prints one JSON summary line on success or a gate rejection, exits 1 on any failure and 143 on SIGTERM: the
contract is in apps/ingest/AGENTS.md ("Commands").
"""

import argparse
import asyncio
import contextlib
import json
import signal
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
    db = Database(settings.database_url)
    store = PgCorpusStore(db)
    publisher = LocalDataPublisher(settings.data_dir)

    async def go_live(version: int) -> None:
        # Local may activate without an eval run; production never does (LLD "6.1 Corpus version").
        await activate(version, store=store, require_eval=settings.app_env != "local")
        folder = await publish_active(version, store=store, publisher=publisher, examples=config.examples)
        log.info("corpus_activated_and_published", version=version, path=folder)

    # Only a finished run or a gate rejection prints a summary: after any other failure, what reached the
    # database is uncertain, and the database (corpus_versions) is the record.
    try:
        async with store.lock() as got:
            if not got:
                log.error("ingest_busy", reason="another ingest run holds the lock")
                return 1
            if args.activate_version is not None:
                await go_live(args.activate_version)
                summary = Summary(args.activate_version, "active")
            else:
                summary = await run_ingest(
                    args.source or corpus_dir(settings.domain),
                    store=store,
                    embedder=build_embedder(settings),
                    scanner=GitleaksScanner(settings.gitleaks_binary),
                    allowlist=config.domain.pii_allowlist,
                    limits=config.limits,
                )
                log.info("corpus_ingested", **asdict(summary))
                if args.activate:
                    await go_live(summary.version)
                    summary = replace(summary, status="active")
        _summary(summary)
        return 0
    except IngestRejected as exc:  # the problems were logged by the pipeline
        _summary(exc.summary)
        return 1
    except ActivationRefused as exc:
        log.error("activation_refused", reason=str(exc))
        return 1
    except PublishFailed as exc:
        # Live but unpublished. Recovery: run the ingest again; it publishes a new version (unchanged chunks
        # are copied, not embedded again).
        log.exception("publish_failed", version=exc.version)
        return 1
    except ConfigError:
        raise  # reported by main()
    except Exception:
        log.exception("ingest_failed")
        return 1
    except BaseException:
        log.warning("ingest_interrupted")
        raise
    finally:
        await db.close()


async def _run(args: argparse.Namespace, settings: IngestSettings, config: _Config) -> int:
    # SIGTERM (docker stop, a job timeout) cancels the run like Ctrl-C, so the cleanup still runs.
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    terminated = False

    def on_sigterm() -> None:
        nonlocal terminated
        terminated = True
        # A second SIGTERM then kills at once instead of cancelling the cleanup halfway.
        loop.remove_signal_handler(signal.SIGTERM)
        if task is not None:
            task.cancel()

    with contextlib.suppress(NotImplementedError):  # no signal handlers on Windows event loops
        loop.add_signal_handler(signal.SIGTERM, on_sigterm)
    try:
        return await _main(args, settings, config)
    except asyncio.CancelledError:
        if terminated:
            return 143  # 128 + SIGTERM
        raise


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
        sys.exit(asyncio.run(_run(args, settings, config)))
    except ConfigError as exc:  # e.g. EMBEDDER=fake outside APP_ENV=local
        print(f"rag-ingest: refusing to run: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
