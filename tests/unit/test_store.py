from datetime import UTC, datetime, timedelta

import pytest

from bpb import models
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend
from bpb.store.repo import Repo
from bpb.store.sheets_schema import SchemaDriftError, canonical_headers, verify_schema


def test_verify_schema_passes_for_canonical_headers():
    verify_schema(canonical_headers())


def test_verify_schema_raises_on_drift():
    headers = canonical_headers()
    drifted = dict(headers)
    drifted["Firms"] = ["id", "an_inserted_column", *headers["Firms"][1:]]
    with pytest.raises(SchemaDriftError):
        verify_schema(drifted)


def test_verify_schema_ignores_tabs_not_yet_created():
    verify_schema({})  # empty workbook — nothing to drift-check yet


def test_bootstrap_creates_all_tabs_and_readme():
    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="test-run")
    for tab in canonical_headers():
        assert tab in repo._rows
    readme_rows = backend.load_rows_with_index("README", ["contents"])
    assert len(readme_rows) == 1


def test_bootstrap_is_idempotent():
    backend = MemoryBackend()
    bootstrap(backend, run_id="run-1")
    bootstrap(backend, run_id="run-2")  # second run must not duplicate the README row
    readme_rows = backend.load_rows_with_index("README", ["contents"])
    assert len(readme_rows) == 1


def test_repo_round_trips_a_firm():
    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="test-run")

    firm = models.Firm(name="Acme Brokerage", domain="acme.example", city="New York")
    repo.upsert_firm(firm)
    repo.flush()

    repo2 = Repo(backend, run_id="test-run-2")
    repo2.load()
    reloaded = repo2.get_firm(firm.id)
    assert reloaded is not None
    assert reloaded.name == "Acme Brokerage"
    assert reloaded.domain == "acme.example"
    assert reloaded.coverage_state == "untouched"


def test_repo_update_then_flush_does_not_duplicate_row():
    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="test-run")

    firm = models.Firm(name="Acme Brokerage")
    repo.upsert_firm(firm)
    repo.flush()

    firm.coverage_state = "path_a_hit"
    repo.upsert_firm(firm)
    repo.flush()

    firm.coverage_state = "exhausted"
    repo.upsert_firm(firm)
    repo.flush()

    rows = backend.load_rows_with_index("Firms", canonical_headers()["Firms"])
    assert len(rows) == 1
    assert rows[0][1]["coverage_state"] == "exhausted"


def test_repo_create_then_update_before_first_flush():
    """A row created and mutated in the same in-memory session, before any flush,
    must still land as exactly one row once flushed."""
    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="test-run")

    firm = models.Firm(name="Acme Brokerage")
    repo.upsert_firm(firm)
    firm.city = "Chicago"
    repo.upsert_firm(firm)  # mutate again before any flush() call
    repo.flush()

    rows = backend.load_rows_with_index("Firms", canonical_headers()["Firms"])
    assert len(rows) == 1
    assert rows[0][1]["city"] == "Chicago"


def test_append_only_tab_preserves_history():
    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="test-run")

    firm = repo.upsert_firm(models.Firm(name="Acme"))
    repo.flush()

    for score in (0.5, 0.9):
        repo.add_signal(
            models.Signal(firm_id=firm.id, url=f"https://example.com/{score}", url_hash=str(score))
        )
    repo.flush()

    assert len(repo.list_signals(firm.id)) == 2


def test_lock_prevents_second_concurrent_acquisition():
    from bpb.runctx import LOCK_NAME

    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="run-a")
    lock = models.Lock(
        name=LOCK_NAME,
        holder_run_id="run-a",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    repo.upsert_lock(lock)
    repo.flush()

    existing = repo.get_lock(LOCK_NAME)
    assert existing is not None
    assert existing.expires_at > datetime.now(UTC)


def test_expired_lock_is_stale():
    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="run-a")
    lock = models.Lock(
        name="global",
        holder_run_id="run-a",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    repo.upsert_lock(lock)
    repo.flush()

    existing = repo.get_lock("global")
    assert existing.expires_at < datetime.now(UTC)
