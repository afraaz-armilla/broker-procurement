"""Storage backends.

`StoreBackend` is the seam that keeps repo.py backend-agnostic: GoogleSheetsBackend
talks to the real workbook; MemoryBackend is a same-interface in-memory stand-in used
for --dry-run, local development, and every unit test (so tests exercise real repo.py
logic — role priority, shortlist, gates, ledger — with zero network calls).

Row representation at this layer is always dict[str, str] (Sheets has no native
types); model (de)serialization happens in repo.py.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Protocol

logger = logging.getLogger(__name__)

RowDict = dict[str, str]


class StoreBackend(Protocol):
    def read_headers(self) -> dict[str, list[str]]:
        """Tab name -> row 1 values, for tabs that currently exist."""
        ...

    def ensure_tabs(self, canonical_headers: dict[str, list[str]]) -> None:
        """Create any missing tab with its header row. Never touches an existing tab."""
        ...

    def load_rows_with_index(self, tab: str, columns: list[str]) -> list[tuple[int, RowDict]]:
        """(1-based sheet row number, row dict) for every data row in `tab`."""
        ...

    def append_rows(self, tab: str, columns: list[str], rows: list[RowDict]) -> int:
        """Append rows; Sheets allocates the row numbers, so concurrent appends are safe.
        Returns the 1-based sheet row number assigned to the FIRST appended row — rows
        land contiguously in order, so row i is at (return value + i)."""
        ...

    def update_rows(self, tab: str, columns: list[str], updates: list[tuple[int, RowDict]]) -> None:
        """Overwrite specific existing rows by their sheet row number."""
        ...


class MemoryBackend:
    """In-memory StoreBackend. Used for --dry-run and all unit tests."""

    def __init__(self) -> None:
        self._headers: dict[str, list[str]] = {}
        self._rows: dict[str, list[RowDict]] = {}

    def read_headers(self) -> dict[str, list[str]]:
        return dict(self._headers)

    def ensure_tabs(self, canonical_headers: dict[str, list[str]]) -> None:
        for tab, cols in canonical_headers.items():
            if tab not in self._headers:
                self._headers[tab] = list(cols)
                self._rows[tab] = []

    def load_rows_with_index(self, tab: str, columns: list[str]) -> list[tuple[int, RowDict]]:
        rows = self._rows.get(tab, [])
        # 1-based, +1 for the header row, matching the real Sheets convention.
        return [(i + 2, row) for i, row in enumerate(rows)]

    def append_rows(self, tab: str, columns: list[str], rows: list[RowDict]) -> int:
        existing = self._rows.setdefault(tab, [])
        start_row = len(existing) + 2
        existing.extend(rows)
        return start_row

    def update_rows(self, tab: str, columns: list[str], updates: list[tuple[int, RowDict]]) -> None:
        existing = self._rows.setdefault(tab, [])
        for sheet_row, row in updates:
            idx = sheet_row - 2
            if 0 <= idx < len(existing):
                existing[idx] = row
            else:
                existing.append(row)


class GoogleSheetsBackend:
    """Real backend: one spreadsheet, accessed via the Sheets API v4."""

    def __init__(self, spreadsheet_id: str, credentials=None) -> None:
        from googleapiclient.discovery import (
            build,  # deferred: heavy import, not needed for tests/dry-run
        )

        self.spreadsheet_id = spreadsheet_id
        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    @property
    def _values(self):
        return self._service.spreadsheets().values()

    def read_headers(self) -> dict[str, list[str]]:
        meta = self._service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        existing_tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
        if not existing_tabs:
            return {}
        ranges = [f"'{t}'!1:1" for t in existing_tabs]
        resp = self._values.batchGet(spreadsheetId=self.spreadsheet_id, ranges=ranges).execute()
        headers: dict[str, list[str]] = {}
        for tab, vr in zip(existing_tabs, resp.get("valueRanges", []), strict=True):
            values = vr.get("values", [])
            headers[tab] = values[0] if values else []
        return headers

    def ensure_tabs(self, canonical_headers: dict[str, list[str]]) -> None:
        existing = set(self.read_headers().keys())
        missing = [t for t in canonical_headers if t not in existing]
        if missing:
            requests = [{"addSheet": {"properties": {"title": tab}}} for tab in missing]
            self._service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id, body={"requests": requests}
            ).execute()
        for tab in missing:
            self._values.update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{tab}'!A1",
                valueInputOption="RAW",
                body={"values": [canonical_headers[tab]]},
            ).execute()

    def load_rows_with_index(self, tab: str, columns: list[str]) -> list[tuple[int, RowDict]]:
        resp = self._values.get(
            spreadsheetId=self.spreadsheet_id, range=f"'{tab}'!A2:{_col(len(columns))}"
        ).execute()
        out: list[tuple[int, RowDict]] = []
        for i, raw in enumerate(resp.get("values", [])):
            padded = (raw + [""] * len(columns))[: len(columns)]
            out.append((i + 2, dict(zip(columns, padded, strict=True))))
        return out

    def append_rows(self, tab: str, columns: list[str], rows: list[RowDict]) -> int:
        if not rows:
            raise ValueError("append_rows called with no rows")
        values = [[row.get(c, "") for c in columns] for row in rows]
        resp = self._values.append(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
        updated_range = resp["updates"]["updatedRange"]  # e.g. "'Signals'!A15:M20"
        return _parse_start_row(updated_range)

    def update_rows(self, tab: str, columns: list[str], updates: list[tuple[int, RowDict]]) -> None:
        if not updates:
            return
        data = [
            {
                "range": f"'{tab}'!A{sheet_row}:{_col(len(columns))}{sheet_row}",
                "values": [[row.get(c, "") for c in columns]],
            }
            for sheet_row, row in updates
        ]
        self._values.batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()


def _parse_start_row(updated_range: str) -> int:
    """"'Signals'!A15:M20" -> 15"""
    cell_ref = updated_range.split("!", 1)[1].split(":", 1)[0]
    digits = "".join(ch for ch in cell_ref if ch.isdigit())
    return int(digits)


def _col(n: int) -> str:
    """1-based column count -> A1 column letter (26 -> Z, 27 -> AA, ...)."""
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def load_google_credentials():
    """Prefer Workload Identity Federation (ADC via GOOGLE_APPLICATION_CREDENTIALS, set by
    google-github-actions/auth) over a stored service-account key. Returns None if neither
    is configured — callers should fall back to MemoryBackend (e.g. local dev, dry-run)."""
    import os

    import google.auth

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]

    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        creds, _ = google.auth.default(scopes=scopes)
        return creds

    key_b64 = os.environ.get("GCP_SERVICE_ACCOUNT_KEY_B64")
    if key_b64:
        from google.oauth2 import service_account

        info = json.loads(base64.b64decode(key_b64))
        return service_account.Credentials.from_service_account_info(info, scopes=scopes)

    logger.warning("No Google credentials configured — falling back to MemoryBackend")
    return None
