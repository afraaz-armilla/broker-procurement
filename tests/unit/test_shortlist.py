from datetime import UTC, datetime, timedelta

from bpb import models
from bpb.selection.shortlist import promote_reserves, select_for_firm


def _prospect(band, score, status="selected", **kwargs) -> models.Prospect:
    return models.Prospect(
        firm_id="f1",
        full_name=f"P{score}",
        role_band=band,
        role_score=score,
        status=status,
        **kwargs,
    )


def test_selects_best_of_each_band():
    band1 = _prospect(1, 100)
    band2 = _prospect(2, 65)
    band3 = _prospect(3, 35)
    extra_band3 = _prospect(3, 30)
    result = select_for_firm([band1, band2, band3, extra_band3], max_active=3)
    assert {p.id for p in result.active} == {band1.id, band2.id, band3.id}
    assert extra_band3 in result.reserved


def test_backfills_when_a_band_is_missing():
    band1 = _prospect(1, 100)
    band3a = _prospect(3, 40)
    band3b = _prospect(3, 35)
    # no band 2 candidate at all
    result = select_for_firm([band1, band3a, band3b], max_active=3)
    assert len(result.active) == 3
    assert {p.id for p in result.active} == {band1.id, band3a.id, band3b.id}


def test_proceeds_with_two_when_that_is_all_that_qualifies():
    band1 = _prospect(1, 100)
    band3 = _prospect(3, 30)
    result = select_for_firm([band1, band3], max_active=3)
    assert len(result.active) == 2
    assert len(result.reserved) == 0


def test_unbanded_prospects_are_excluded_entirely():
    band1 = _prospect(1, 100)
    unbanded = _prospect(None, 0.0)
    result = select_for_firm([band1, unbanded], max_active=3)
    assert unbanded not in result.active
    assert unbanded not in result.reserved


def test_reserve_rank_assigned_best_first():
    band1 = _prospect(1, 100)
    band2 = _prospect(2, 65)
    band3 = _prospect(3, 40)
    reserve_a = _prospect(3, 35)
    reserve_b = _prospect(3, 20)
    result = select_for_firm([band1, band2, band3, reserve_a, reserve_b], max_active=3)
    assert result.reserved[0].id == reserve_a.id
    assert result.reserved[0].reserve_rank == 1
    assert result.reserved[1].id == reserve_b.id
    assert result.reserved[1].reserve_rank == 2


def test_promote_reserves_fills_open_slot_from_rejected_active():
    now = datetime.now(UTC)
    active = [
        _prospect(1, 100, status="rejected"),
        _prospect(2, 65, status="selected"),
        _prospect(3, 40, status="selected"),
    ]
    reserve = _prospect(3, 35, status="reserved", reserve_rank=1)
    promoted = promote_reserves(active, [reserve], max_active=3, stale_after_days=45, now=now)
    assert promoted == [reserve]


def test_promote_reserves_fills_open_slot_from_stale_active():
    now = datetime.now(UTC)
    stale_active = _prospect(1, 100, status="selected", last_activity_at=now - timedelta(days=100))
    active = [stale_active, _prospect(2, 65), _prospect(3, 40)]
    reserve = _prospect(3, 35, status="reserved", reserve_rank=1)
    promoted = promote_reserves(active, [reserve], max_active=3, stale_after_days=45, now=now)
    assert promoted == [reserve]


def test_promote_reserves_no_promotion_when_firm_at_capacity():
    active = [_prospect(1, 100), _prospect(2, 65), _prospect(3, 40)]
    reserve = _prospect(3, 35, status="reserved", reserve_rank=1)
    promoted = promote_reserves(active, [reserve], max_active=3, stale_after_days=45)
    assert promoted == []


def test_promote_reserves_prefers_newly_signaled_reserve_over_older_lower_band_reserve():
    """A reserve who picked up a new Path A signal gets re-banded to band 1
    upstream (by role_priority) before this runs — this test checks that once
    re-banded, they correctly sort ahead of a same-or-lower-band reserve."""
    active = [_prospect(1, 100, status="rejected"), _prospect(2, 65), _prospect(3, 40)]
    newly_signaled = _prospect(1, 105, status="reserved", reserve_rank=2)  # re-banded to 1
    older_reserve = _prospect(3, 38, status="reserved", reserve_rank=1)
    promoted = promote_reserves(
        active, [older_reserve, newly_signaled], max_active=3, stale_after_days=45
    )
    assert promoted == [newly_signaled]
