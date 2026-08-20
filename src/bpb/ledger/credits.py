"""Credit budget enforcement — the guard on the scarcest resource (Apollo/Hunter/
ZeroBounce/Abstract free-tier credits). Two ideas from the build plan drive this
module's shape:

1. Write-ahead: `reserve()` appends the CreditLedger row and flushes it BEFORE the
   caller makes the paid API call. A crash mid-call then over-counts spend rather
   than under-counting it — the safe direction for a budget guard. Because
   CreditLedger is append-only (see store/sheets_schema.py), there's no post-call
   "correction" step: `reserve()` is passed the worst-case credits for that
   endpoint (e.g. 1 for a single ZeroBounce validate call), and that is what gets
   recorded permanently. Any drift this creates against a provider's own usage
   endpoint is what `reconcile()` surfaces (as a warning, not a rewrite).
2. Degradation ladder: warn/degrade/hard_stop thresholds per bucket, each checked
   against BOTH our own running total this run AND (once fetched) the provider's
   own live remaining-credits count — whichever is more pessimistic wins.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from bpb import models
from bpb.config import BudgetSettings
from bpb.models import CreditBucket, CreditProvider
from bpb.store.repo import Repo

logger = logging.getLogger(__name__)

DegradeTier = Literal["ok", "warn", "degrade", "hard_stop"]

BUCKET_TO_PROVIDER: dict[CreditBucket, CreditProvider] = {
    "apollo_lead_credit": "apollo",
    "hunter_search": "hunter",
    "zerobounce_verification": "zerobounce",
    "abstract_verification": "abstract",
}


@dataclass(frozen=True)
class BudgetStatus:
    bucket: CreditBucket
    local_spent: int
    cap: int
    live_remaining: int | None
    pct_used: float
    tier: DegradeTier


class CreditLedger:
    def __init__(self, repo: Repo, settings: BudgetSettings, run_id: str) -> None:
        self.repo = repo
        self.settings = settings
        self.run_id = run_id
        self._live_remaining: dict[str, int | None] = {}

    # -- live provider usage, fetched at most once per run per bucket ---------

    def set_live_remaining(self, bucket: CreditBucket, remaining: int | None) -> None:
        """Callers fetch each provider's own usage endpoint once per run (a free
        call — see clients/*.get_credits()-style methods) and record it here so
        status() can use ground truth instead of trusting our own log indefinitely."""
        self._live_remaining[bucket] = remaining

    def fetch_live_remaining_safely(
        self, bucket: CreditBucket, fetch_fn: Callable[[], int]
    ) -> None:
        try:
            self.set_live_remaining(bucket, fetch_fn())
        except Exception:
            logger.warning("Could not fetch live remaining credits for %s", bucket, exc_info=True)
            self.set_live_remaining(bucket, None)

    # -- status / degradation ---------------------------------------------------

    def local_spent(self, bucket: CreditBucket) -> int:
        return sum(
            e.credits_charged for e in self.repo.list_credit_ledger_entries() if e.bucket == bucket
        )

    def status(self, bucket: CreditBucket) -> BudgetStatus:
        cfg = self.settings.bucket(bucket)
        spent = self.local_spent(bucket)
        live = self._live_remaining.get(bucket)
        pct = (spent / cfg.monthly_cap * 100) if cfg.monthly_cap else 100.0

        if pct >= cfg.hard_stop_at_pct or (live is not None and live <= 0):
            tier: DegradeTier = "hard_stop"
        elif pct >= cfg.degrade_at_pct:
            tier = "degrade"
        elif pct >= cfg.warn_at_pct:
            tier = "warn"
        else:
            tier = "ok"
        return BudgetStatus(
            bucket=bucket, local_spent=spent, cap=cfg.monthly_cap, live_remaining=live,
            pct_used=pct, tier=tier,
        )

    def can_spend(self, bucket: CreditBucket) -> bool:
        return self.status(bucket).tier != "hard_stop"

    def path_b_degraded(self) -> bool:
        """True once ANY Path B-relevant bucket has crossed degrade_at_pct — the
        pipeline's assemble stage falls back to Path A only when this is true
        (Path A costs no lookup credits, per the build plan's §11 ladder)."""
        path_b_buckets: tuple[CreditBucket, ...] = ("apollo_lead_credit", "hunter_search")
        return any(self.status(b).tier in ("degrade", "hard_stop") for b in path_b_buckets)

    # -- recording ---------------------------------------------------------------

    def reserve(
        self,
        *,
        bucket: CreditBucket,
        endpoint: str,
        credits: int,
        prospect_id: str | None = None,
        firm_id: str | None = None,
        note: str = "",
    ) -> models.CreditLedgerEntry:
        """Write-ahead: call this BEFORE issuing the paid request, with the
        worst-case credit cost for that endpoint. Flushes immediately — this flush
        IS the durability guarantee (see module docstring)."""
        provider = BUCKET_TO_PROVIDER[bucket]
        entry = models.CreditLedgerEntry(
            provider=provider,
            bucket=bucket,
            endpoint=endpoint,
            credits_charged=credits,
            prospect_id=prospect_id,
            firm_id=firm_id,
            run_id=self.run_id,
            provider_reported_remaining=self._live_remaining.get(bucket),
            note=note,
        )
        self.repo.add_credit_ledger_entry(entry)
        self.repo.flush()
        return entry

    def reconcile(self, bucket: CreditBucket, provider_reported_delta: int) -> bool:
        """Compare our local spend this run against a delta the provider reports
        (their remaining-before minus remaining-after). Returns True if within
        tolerance. A drift beyond tolerance means our cost model for this bucket
        is wrong somewhere — that's the thing worth alerting on, not the drift
        itself (see ledger/alerts.py)."""
        spent = self.local_spent(bucket)
        tolerance = max(2, int(spent * 0.1))
        return abs(spent - provider_reported_delta) <= tolerance


def report(repo: Repo) -> str:
    entries = repo.list_credit_ledger_entries()
    by_bucket: dict[str, int] = {}
    for e in entries:
        by_bucket[e.bucket] = by_bucket.get(e.bucket, 0) + e.credits_charged
    if not by_bucket:
        return "No credit ledger entries yet."
    lines = [f"{bucket}: {spent} credits spent" for bucket, spent in sorted(by_bucket.items())]
    return "\n".join(lines)
