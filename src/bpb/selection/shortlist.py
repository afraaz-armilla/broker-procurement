"""Pure 2-3-per-firm selection (§6). Archetype coverage, not top-N-by-score: the
best Band 1 + best Band 2 + best Band 3, backfilling from whatever's left if a
band is empty, capped at max_active_per_firm. Everyone else becomes `reserved`
with a rank; `promote_reserves` decides who moves up when a slot frees.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from bpb import models

TERMINAL_STATUSES = {
    "rejected",
    "disqualified_sanctions",
    "disqualified_email",
    "suppressed",
}


@dataclass(frozen=True)
class ShortlistResult:
    active: list[models.Prospect]
    reserved: list[models.Prospect]  # reserve_rank set, best (lowest rank) first


def select_for_firm(
    prospects: list[models.Prospect], *, max_active: int = 3
) -> ShortlistResult:
    banded = [p for p in prospects if p.role_band is not None]
    banded_sorted = sorted(banded, key=lambda p: p.role_score, reverse=True)

    chosen: list[models.Prospect] = []
    chosen_ids: set[str] = set()
    for band in (1, 2, 3):
        best = next(
            (p for p in banded_sorted if p.role_band == band and p.id not in chosen_ids), None
        )
        if best is not None:
            chosen.append(best)
            chosen_ids.add(best.id)

    for p in banded_sorted:
        if len(chosen) >= max_active:
            break
        if p.id not in chosen_ids:
            chosen.append(p)
            chosen_ids.add(p.id)

    reserved = [p for p in banded_sorted if p.id not in chosen_ids]
    for rank, p in enumerate(reserved, start=1):
        p.reserve_rank = rank

    return ShortlistResult(active=chosen, reserved=reserved)


def _is_stale(prospect: models.Prospect, *, now: datetime, stale_after_days: int) -> bool:
    if prospect.last_activity_at is None:
        return False
    last_activity = prospect.last_activity_at
    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=UTC)
    return (now - last_activity).days >= stale_after_days


def promote_reserves(
    active: list[models.Prospect],
    reserved: list[models.Prospect],
    *,
    max_active: int,
    stale_after_days: int,
    now: datetime | None = None,
) -> list[models.Prospect]:
    """Which reserves should move to active this cycle. Does not mutate anything —
    the caller applies the promotion (status/reserve_rank updates) and persists.

    A reserve who picked up a new Path A signal since the last cycle should
    already have been re-banded to Band 1 with a high score by role_priority
    BEFORE this is called — that's what makes them sort ahead of other reserves
    here, no special-casing needed in this function.
    """
    now = now or datetime.now(UTC)
    effective_active = [
        p
        for p in active
        if p.status not in TERMINAL_STATUSES
        and not _is_stale(p, now=now, stale_after_days=stale_after_days)
    ]
    open_slots = max_active - len(effective_active)
    if open_slots <= 0:
        return []

    def sort_key(p: models.Prospect) -> tuple[int, float, int]:
        band = p.role_band if p.role_band is not None else 99
        return (band, -p.role_score, p.reserve_rank or 0)

    ordered = sorted(reserved, key=sort_key)
    return ordered[:open_slots]
