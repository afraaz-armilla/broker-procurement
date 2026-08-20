"""Canonical Google Sheets tab/column definitions.

This is the single source of truth for what a healthy workbook looks like. It is
deliberately independent of models.py's field order at the type-checker level (a
model field rename is a code change; a header drift on the live sheet is a data
incident) even though in practice each tab's columns are generated from its model
below — `verify_schema()` is what actually catches a human-edited header row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import get_type_hints

from bpb import models

TabName = str


@dataclass(frozen=True)
class TabSpec:
    name: TabName
    model: type[models.BpbModel]
    append_only: bool
    id_key: str = "id"  # column that uniquely identifies a logical row/entity


TAB_SPECS: dict[TabName, TabSpec] = {
    "Firms": TabSpec("Firms", models.Firm, append_only=False),
    "Prospects": TabSpec("Prospects", models.Prospect, append_only=False),
    "Signals": TabSpec("Signals", models.Signal, append_only=True),
    "Emails": TabSpec("Emails", models.Email, append_only=True),
    "Verifications": TabSpec("Verifications", models.Verification, append_only=True),
    "Screenings": TabSpec("Screenings", models.Screening, append_only=True),
    "SanctionsSnapshots": TabSpec(
        "SanctionsSnapshots", models.SanctionsSnapshot, append_only=True
    ),
    "CreditLedger": TabSpec("CreditLedger", models.CreditLedgerEntry, append_only=True),
    "QueueItems": TabSpec("QueueItems", models.QueueItem, append_only=False),
    "OutreachLog": TabSpec("OutreachLog", models.OutreachLogEntry, append_only=True),
    "Suppressions": TabSpec("Suppressions", models.Suppression, append_only=False),
    "Runs": TabSpec("Runs", models.Run, append_only=True),
    "Locks": TabSpec("Locks", models.Lock, append_only=False),
}

# Columns that exist on every bot-owned tab but are never written by the bot.
HUMAN_EDITABLE_COLUMNS = {"notes"}

README_CONTENTS = """\
This workbook is written and read by the Broker Prospecting Bot (see the
`broker-procurement` repo). A few rules keep it safe to hand-edit:

1. Never insert, delete, or reorder columns on any tab — the bot verifies the
   header row on every run and will refuse to write if it drifts.
2. The `notes` column on every tab is yours: the bot never reads or writes it.
3. Everything else is bot-owned. Editing `QueueItems.edited_body` is the one
   sanctioned exception — the bot's approval poller reads a Slack thread reply
   for edits, not a Sheets edit; edit in Slack, not here.
4. Tabs marked append-only in the bot's schema (Signals, Emails, Verifications,
   Screenings, SanctionsSnapshots, CreditLedger, OutreachLog, Runs) should not
   have rows deleted or edited — the bot treats the latest row per id as current
   history and an edited past row breaks that.
5. Sanctions list entries themselves are not in this workbook (too large) — see
   the SanctionsSnapshots tab for pointers to the archived raw files in Drive.
"""


def columns_for(spec: TabSpec) -> list[str]:
    """Column order for a tab, derived from its model's field declaration order."""
    hints = get_type_hints(spec.model)
    return [name for name in spec.model.model_fields if name in hints]


def canonical_headers() -> dict[TabName, list[str]]:
    return {name: columns_for(spec) for name, spec in TAB_SPECS.items()}


class SchemaDriftError(RuntimeError):
    def __init__(self, tab: TabName, expected: list[str], found: list[str]):
        self.tab = tab
        self.expected = expected
        self.found = found
        super().__init__(
            f"Schema drift on tab {tab!r}: expected header {expected}, found {found}. "
            "Refusing to write — restore the header row or update sheets_schema.py "
            "deliberately if this is an intentional migration."
        )


def verify_schema(live_headers: dict[TabName, list[str]]) -> None:
    """Raise SchemaDriftError on the first tab whose live header doesn't match ours.

    `live_headers` is tab name -> row 1 values as read from the workbook. A tab
    missing from `live_headers` (not yet created) is not drift — see bootstrap.py.
    """
    for name, expected in canonical_headers().items():
        if name not in live_headers:
            continue
        found = live_headers[name]
        if found != expected:
            raise SchemaDriftError(name, expected, found)
