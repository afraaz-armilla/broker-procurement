"""G2 refresh: download the four government sanctions lists, archive the raw bytes
to Drive (the audit trail — see clients/gdrive.py), record a SanctionsSnapshot per
list, parse into SanctionsEntry records, and rebuild the local match index that
matcher.screen_name() reads. Run daily (see .github/workflows/sanctions-refresh.yml).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import cast

import httpx
import yaml

from bpb import models
from bpb.clients.gdrive import DriveArchiveClient
from bpb.config import CONFIG_DIR, Secrets
from bpb.gates.sanctions import parsers
from bpb.gates.sanctions.matcher import build_index, save_index
from bpb.store.db import load_google_credentials
from bpb.store.repo import Repo

logger = logging.getLogger(__name__)

# Minimal but well-formed content per format, used only in --dry-run so the parse
# step is genuinely exercised (not skipped) without any network call.
_DRY_RUN_FIXTURES: dict[str, bytes] = {
    "ofac_sdn": b'1,"Fixture Test Entry","individual","FIXTURE-PROGRAM","-0-","-0-"\n',
    "ofac_consolidated": b'1,"Fixture Consolidated Entry","entity","FIXTURE-PROGRAM","-0-","-0-"\n',
    "uksl": (
        b"<Designations><Designation><Name><Name6>Fixture Test Entry</Name6>"
        b"</Name></Designation></Designations>"
    ),
    "canada_sema": (
        b"<Records><Record><LastName>Fixture</LastName><GivenName>Test</GivenName>"
        b"</Record></Records>"
    ),
    "un_consolidated": (
        b"<CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL><FIRST_NAME>Fixture</FIRST_NAME>"
        b"<SECOND_NAME>Test</SECOND_NAME></INDIVIDUAL></INDIVIDUALS></CONSOLIDATED_LIST>"
    ),
}


def _load_sources() -> dict[str, dict]:
    with (CONFIG_DIR / "sanctions.yaml").open() as f:
        return yaml.safe_load(f)["sources"]


def _fetch(list_source: str, url: str, *, dry_run: bool) -> bytes:
    if dry_run:
        return _DRY_RUN_FIXTURES.get(list_source, b"")
    response = httpx.get(
        url, timeout=60.0, follow_redirects=True, headers={"User-Agent": "ArmillaBrokerBot/1.0"}
    )
    response.raise_for_status()
    return response.content


def refresh_all(repo: Repo, *, dry_run: bool = False) -> list[models.SanctionsSnapshot]:
    sources = _load_sources()
    secrets = Secrets()
    drive = DriveArchiveClient(
        secrets.google_drive_folder_id or "",
        credentials=None if dry_run else load_google_credentials(),
        dry_run=dry_run,
    )

    snapshots: list[models.SanctionsSnapshot] = []
    all_entries: list[parsers.SanctionsEntry] = []

    for list_source_key, cfg in sources.items():
        list_source = cast(models.SanctionsListSource, list_source_key)
        url = cfg["url"]
        try:
            content = _fetch(list_source, url, dry_run=dry_run)
        except httpx.HTTPError:
            logger.warning("Failed to fetch %s from %s", list_source, url, exc_info=True)
            continue

        sha256 = hashlib.sha256(content).hexdigest()
        parser = parsers.PARSERS[list_source]
        entries = parser(content) if content else []
        all_entries.extend(entries)

        ext = "xml" if url.lower().endswith("xml") else "csv"
        filename = f"{list_source}/{datetime.now(UTC):%Y-%m-%d}-{sha256[:8]}.{ext}"
        drive_file_id = drive.upload_raw_list(filename, content)

        snapshot = models.SanctionsSnapshot(
            list_source=list_source,
            url=url,
            sha256=sha256,
            entry_count=len(entries),
            drive_file_id=drive_file_id,
        )
        repo.add_sanctions_snapshot(snapshot)
        snapshots.append(snapshot)

    repo.flush()

    snapshot_ids: dict[str, str] = {str(s.list_source): s.id for s in snapshots}
    index = build_index(all_entries, snapshot_ids=snapshot_ids)
    save_index(index)
    logger.info(
        "Sanctions index rebuilt: %d entries across %d lists", len(all_entries), len(snapshots)
    )
    return snapshots
