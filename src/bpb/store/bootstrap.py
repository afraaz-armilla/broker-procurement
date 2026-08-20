"""First-run setup: create every tab with its header row, write the README tab,
and (on the real backend) protect header rows and bot-owned columns.

Idempotent — safe to run against an already-initialized workbook; it only creates
what's missing.
"""

from __future__ import annotations

import logging

from bpb.store.db import GoogleSheetsBackend, StoreBackend
from bpb.store.repo import Repo
from bpb.store.sheets_schema import README_CONTENTS, canonical_headers

logger = logging.getLogger(__name__)

README_TAB = "README"


def bootstrap(backend: StoreBackend, run_id: str) -> Repo:
    """Ensure all tabs exist with correct headers, seed README, return a loaded Repo."""
    canonical = canonical_headers()
    backend.ensure_tabs(canonical)
    _write_readme(backend)

    repo = Repo(backend, run_id)
    repo.load()

    if isinstance(backend, GoogleSheetsBackend):
        _protect_header_rows(backend, list(canonical.keys()))

    return repo


def _write_readme(backend: StoreBackend) -> None:
    # README isn't a data tab (no model), so it's handled directly rather than via repo.
    backend.ensure_tabs({README_TAB: ["contents"]})
    existing = backend.load_rows_with_index(README_TAB, ["contents"])
    if existing:
        return  # already seeded, never overwrite — Phil may have added his own notes below it
    backend.append_rows(README_TAB, ["contents"], [{"contents": README_CONTENTS}])


def _protect_header_rows(backend: GoogleSheetsBackend, tabs: list[str]) -> None:
    """Best-effort: protect row 1 (headers) on every bot-owned tab so a stray edit
    can't silently shift every subsequent write. Failures are logged, not fatal —
    protection is a safety net, not a correctness requirement (verify_schema() is
    the actual guard)."""
    try:
        meta = backend._service.spreadsheets().get(spreadsheetId=backend.spreadsheet_id).execute()
        sheet_ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
        requests = []
        for tab in tabs:
            sheet_id = sheet_ids.get(tab)
            if sheet_id is None:
                continue
            requests.append(
                {
                    "addProtectedRange": {
                        "protectedRange": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                            },
                            "description": f"bpb: {tab} header row — do not edit",
                            "warningOnly": True,
                        }
                    }
                }
            )
        if requests:
            backend._service.spreadsheets().batchUpdate(
                spreadsheetId=backend.spreadsheet_id, body={"requests": requests}
            ).execute()
    except Exception:  # pragma: no cover - best-effort safety net
        logger.warning("Could not set protected ranges on header rows", exc_info=True)
