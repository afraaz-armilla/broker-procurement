"""The stable typed interface every other module codes against.

Backend-agnostic: construct with a MemoryBackend for tests/dry-run or a
GoogleSheetsBackend for real runs — nothing above this module knows which.

Lifecycle: load() once at run start (batchGet under the hood), mutate freely in
memory via the typed methods below (free, no I/O), flush() at each pipeline stage
boundary and in a `finally`. See docs/runbook.md for the stage-boundary convention.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, TypeVar

from bpb import models
from bpb.store.db import StoreBackend
from bpb.store.sheets_schema import TAB_SPECS, canonical_headers, verify_schema

M = TypeVar("M", bound=models.BpbModel)


def _serialize(model: models.BpbModel, columns: list[str]) -> dict[str, str]:
    dumped = json.loads(model.model_dump_json())
    row: dict[str, str] = {}
    for col in columns:
        value = dumped.get(col)
        if value is None:
            row[col] = ""
        elif isinstance(value, bool):
            row[col] = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            row[col] = json.dumps(value)
        else:
            row[col] = str(value)
    return row


def _deserialize[M2: models.BpbModel](model_cls: type[M2], row: dict[str, str]) -> M2:
    data: dict[str, Any] = {}
    hints = model_cls.model_fields
    for col, raw in row.items():
        if col not in hints or raw == "":
            continue
        field = hints[col]
        ann = field.annotation
        if _is_bool_field(ann):
            data[col] = raw.lower() == "true"
        elif _is_int_field(ann) and not _is_datetime_field(ann):
            try:
                data[col] = int(raw)
            except ValueError:
                data[col] = raw
        else:
            data[col] = raw
    return model_cls.model_validate(data)


def _is_bool_field(ann: Any) -> bool:
    return ann is bool or _unwrap_optional(ann) is bool


def _is_int_field(ann: Any) -> bool:
    return ann is int or _unwrap_optional(ann) is int


def _is_datetime_field(ann: Any) -> bool:
    return ann is datetime or _unwrap_optional(ann) is datetime


def _unwrap_optional(ann: Any) -> Any:
    args = getattr(ann, "__args__", None)
    if args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return ann


class Repo:
    def __init__(self, backend: StoreBackend, run_id: str) -> None:
        self.backend = backend
        self.run_id = run_id
        self._rows: dict[str, dict[str, tuple[int | None, models.BpbModel]]] = {
            t: {} for t in TAB_SPECS
        }
        self._dirty: dict[str, set[str]] = {t: set() for t in TAB_SPECS}
        self._loaded = False

    # -- lifecycle -----------------------------------------------------------

    def load(self) -> None:
        headers = self.backend.read_headers()
        verify_schema(headers)
        canonical = canonical_headers()
        self.backend.ensure_tabs(canonical)
        headers = self.backend.read_headers()
        for tab, spec in TAB_SPECS.items():
            cols = canonical[tab]
            table: dict[str, tuple[int | None, models.BpbModel]] = {}
            for sheet_row, raw in self.backend.load_rows_with_index(tab, cols):
                model = _deserialize(spec.model, raw)
                table[model.id] = (sheet_row, model)
            self._rows[tab] = table
        self._loaded = True

    def flush(self) -> None:
        canonical = canonical_headers()
        for tab in TAB_SPECS:
            cols = canonical[tab]
            dirty_ids = self._dirty[tab]
            if not dirty_ids:
                continue
            to_append: list[str] = []
            to_update: list[tuple[int, dict[str, str]]] = []
            for id_ in dirty_ids:
                sheet_row, model = self._rows[tab][id_]
                if sheet_row is None:
                    to_append.append(id_)
                else:
                    to_update.append((sheet_row, _serialize(model, cols)))
            if to_append:
                rows = [_serialize(self._rows[tab][id_][1], cols) for id_ in to_append]
                start_row = self.backend.append_rows(tab, cols, rows)
                for offset, id_ in enumerate(to_append):
                    _, model = self._rows[tab][id_]
                    self._rows[tab][id_] = (start_row + offset, model)
            if to_update:
                self.backend.update_rows(tab, cols, to_update)
            dirty_ids.clear()

    # -- generic CRUD, used by the typed wrappers below -----------------------

    def _get(self, tab: str, id_: str) -> models.BpbModel | None:
        entry = self._rows[tab].get(id_)
        return entry[1] if entry else None

    def _list(self, tab: str) -> list[models.BpbModel]:
        return [model for _, model in self._rows[tab].values()]

    def _put(self, tab: str, model: models.BpbModel, *, is_new: bool) -> None:
        existing_row = self._rows[tab].get(model.id)
        sheet_row = None if is_new else (existing_row[0] if existing_row else None)
        self._rows[tab][model.id] = (sheet_row, model)
        self._dirty[tab].add(model.id)

    def append(self, tab: str, model: M) -> M:
        self._put(tab, model, is_new=True)
        return model

    def update(self, tab: str, model: models.MutableModel) -> None:
        model.updated_at = datetime.now(UTC)
        model.row_version += 1
        self._put(tab, model, is_new=False)

    # -- typed wrappers --------------------------------------------------------

    def get_firm(self, firm_id: str) -> models.Firm | None:
        return self._get("Firms", firm_id)  # type: ignore[return-value]

    def get_firm_by_domain(self, domain: str) -> models.Firm | None:
        domain = domain.lower()
        for firm in self.list_firms():
            if (firm.domain or "").lower() == domain:
                return firm
        return None

    def list_firms(self) -> list[models.Firm]:
        return self._list("Firms")  # type: ignore[return-value]

    def upsert_firm(self, firm: models.Firm) -> models.Firm:
        if self._get("Firms", firm.id) is None:
            return self.append("Firms", firm)
        self.update("Firms", firm)
        return firm

    def get_prospect(self, prospect_id: str) -> models.Prospect | None:
        return self._get("Prospects", prospect_id)  # type: ignore[return-value]

    def list_prospects(self, firm_id: str | None = None) -> list[models.Prospect]:
        prospects: list[models.Prospect] = self._list("Prospects")  # type: ignore[assignment]
        if firm_id is not None:
            prospects = [p for p in prospects if p.firm_id == firm_id]
        return prospects

    def upsert_prospect(self, prospect: models.Prospect) -> models.Prospect:
        if self._get("Prospects", prospect.id) is None:
            return self.append("Prospects", prospect)
        self.update("Prospects", prospect)
        return prospect

    def add_signal(self, signal: models.Signal) -> models.Signal:
        return self.append("Signals", signal)

    def list_signals(self, firm_id: str | None = None) -> list[models.Signal]:
        signals: list[models.Signal] = self._list("Signals")  # type: ignore[assignment]
        if firm_id is not None:
            signals = [s for s in signals if s.firm_id == firm_id]
        return signals

    def add_email(self, email: models.Email) -> models.Email:
        return self.append("Emails", email)

    def list_emails(self, prospect_id: str) -> list[models.Email]:
        emails: list[models.Email] = self._list("Emails")  # type: ignore[assignment]
        return [e for e in emails if e.prospect_id == prospect_id]

    def add_verification(self, verification: models.Verification) -> models.Verification:
        return self.append("Verifications", verification)

    def add_screening(self, screening: models.Screening) -> models.Screening:
        return self.append("Screenings", screening)

    def add_sanctions_snapshot(
        self, snapshot: models.SanctionsSnapshot
    ) -> models.SanctionsSnapshot:
        return self.append("SanctionsSnapshots", snapshot)

    def list_sanctions_snapshots(self) -> list[models.SanctionsSnapshot]:
        return self._list("SanctionsSnapshots")  # type: ignore[return-value]

    def add_credit_ledger_entry(self, entry: models.CreditLedgerEntry) -> models.CreditLedgerEntry:
        return self.append("CreditLedger", entry)

    def list_credit_ledger_entries(self) -> list[models.CreditLedgerEntry]:
        return self._list("CreditLedger")  # type: ignore[return-value]

    def get_queue_item(self, queue_item_id: str) -> models.QueueItem | None:
        return self._get("QueueItems", queue_item_id)  # type: ignore[return-value]

    def list_queue_items(self, decision: str | None = None) -> list[models.QueueItem]:
        items: list[models.QueueItem] = self._list("QueueItems")  # type: ignore[assignment]
        if decision is not None:
            items = [i for i in items if i.decision == decision]
        return items

    def upsert_queue_item(self, item: models.QueueItem) -> models.QueueItem:
        if self._get("QueueItems", item.id) is None:
            return self.append("QueueItems", item)
        self.update("QueueItems", item)
        return item

    def add_outreach_log(self, entry: models.OutreachLogEntry) -> models.OutreachLogEntry:
        return self.append("OutreachLog", entry)

    def list_outreach_log(self, prospect_id: str | None = None) -> list[models.OutreachLogEntry]:
        entries: list[models.OutreachLogEntry] = self._list("OutreachLog")  # type: ignore[assignment]
        if prospect_id is not None:
            entries = [e for e in entries if e.prospect_id == prospect_id]
        return entries

    def list_suppressions(self) -> list[models.Suppression]:
        return self._list("Suppressions")  # type: ignore[return-value]

    def is_suppressed(self, scope: str, key_normalized: str) -> bool:
        for s in self.list_suppressions():
            if s.scope == scope and s.key_normalized == key_normalized and s.released_at is None:
                return True
        return False

    def upsert_suppression(self, suppression: models.Suppression) -> models.Suppression:
        if self._get("Suppressions", suppression.id) is None:
            return self.append("Suppressions", suppression)
        self.update("Suppressions", suppression)
        return suppression

    def add_run_event(self, run: models.Run) -> models.Run:
        return self.append("Runs", run)

    def list_runs(self) -> list[models.Run]:
        return self._list("Runs")  # type: ignore[return-value]

    def get_lock(self, name: str) -> models.Lock | None:
        for lock in self._list("Locks"):
            if lock.name == name:  # type: ignore[attr-defined]
                return lock  # type: ignore[return-value]
        return None

    def upsert_lock(self, lock: models.Lock) -> models.Lock:
        existing = self.get_lock(lock.name)
        if existing is None:
            return self.append("Locks", lock)
        lock.id = existing.id
        self.update("Locks", lock)
        return lock
