"""Run bookkeeping: one RunContext per CLI invocation, threading run_id/mode/dry_run
through the pipeline and wrapping every command in a Runs "started"/"finished" pair
plus the global advisory lock (see store/repo.py Locks tab and docs/runbook.md §3.4).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal

from bpb import models
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend, StoreBackend
from bpb.store.repo import Repo

logger = logging.getLogger(__name__)

RunMode = Literal["live", "dry_run", "validation"]

LOCK_NAME = "global"
LOCK_TTL_SECONDS = 900  # a run should never legitimately hold the lock this long


class LockHeldError(RuntimeError):
    pass


def build_backend(dry_run: bool) -> StoreBackend:
    if dry_run:
        return MemoryBackend()
    from bpb.config import Secrets
    from bpb.store.db import GoogleSheetsBackend, load_google_credentials

    secrets = Secrets()
    secrets.require("google_sheets_spreadsheet_id")
    assert secrets.google_sheets_spreadsheet_id is not None  # guaranteed by require() above
    creds = load_google_credentials()
    if creds is None:
        logger.warning("No Google credentials found; using MemoryBackend (nothing will persist)")
        return MemoryBackend()
    return GoogleSheetsBackend(secrets.google_sheets_spreadsheet_id, credentials=creds)


@contextmanager
def run_context(command: str, mode: RunMode, dry_run: bool) -> Iterator[Repo]:
    run_id = models.new_id()
    backend = build_backend(dry_run)
    repo = bootstrap(backend, run_id)

    _acquire_lock(repo, run_id)
    started = models.Run(run_id=run_id, command=command, mode=mode, event="started")
    repo.add_run_event(started)
    repo.flush()

    status = "success"
    try:
        yield repo
    except BaseException:
        status = "failed"
        raise
    finally:
        finished = models.Run(
            run_id=run_id,
            command=command,
            mode=mode,
            event="finished",
            status=status,
            stats_json=json.dumps({}),
        )
        repo.add_run_event(finished)
        _release_lock(repo, run_id)
        repo.flush()


def _acquire_lock(repo: Repo, run_id: str) -> None:
    now = datetime.now(UTC)
    existing = repo.get_lock(LOCK_NAME)
    if existing is not None and existing.expires_at > now:
        raise LockHeldError(
            f"Global lock held by run {existing.holder_run_id} "
            f"until {existing.expires_at.isoformat()}"
        )
    lock = models.Lock(
        name=LOCK_NAME, holder_run_id=run_id, expires_at=now + timedelta(seconds=LOCK_TTL_SECONDS)
    )
    repo.upsert_lock(lock)
    repo.flush()


def _release_lock(repo: Repo, run_id: str) -> None:
    existing = repo.get_lock(LOCK_NAME)
    if existing is not None and existing.holder_run_id == run_id:
        existing.expires_at = datetime.now(UTC)
        repo.upsert_lock(existing)


def git_sha() -> str | None:
    return os.environ.get("GITHUB_SHA")
