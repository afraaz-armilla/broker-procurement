import pytest

from bpb.config import BudgetBucket, BudgetSettings
from bpb.ledger.alerts import check_and_alert
from bpb.ledger.credits import CreditLedger
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend


def _settings(cap: int = 100) -> BudgetSettings:
    bucket = BudgetBucket(monthly_cap=cap, warn_at_pct=70, degrade_at_pct=85, hard_stop_at_pct=95)
    return BudgetSettings(
        apollo_lead_credit=bucket,
        hunter_search=bucket,
        zerobounce_verification=bucket,
        abstract_verification=bucket,
    )


def _ledger(cap: int = 100) -> CreditLedger:
    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="test-run")
    return CreditLedger(repo, _settings(cap), run_id="test-run")


def test_reserve_appends_and_accumulates_local_spend():
    ledger = _ledger()
    ledger.reserve(bucket="apollo_lead_credit", endpoint="mixed_people_search", credits=1)
    ledger.reserve(bucket="apollo_lead_credit", endpoint="people_match", credits=1)
    assert ledger.local_spent("apollo_lead_credit") == 2


def test_reserve_writes_are_flushed_immediately_write_ahead():
    """A crash right after reserve() must not lose the reservation — flush()
    happens inside reserve(), not deferred to a later stage boundary."""
    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="run-a")
    ledger = CreditLedger(repo, _settings(), run_id="run-a")
    ledger.reserve(bucket="hunter_search", endpoint="domain_search", credits=1)

    # A second, independent Repo reading the same backend sees the write —
    # simulating "process crashed after reserve(), before anything else".
    from bpb.store.repo import Repo

    repo2 = Repo(backend, run_id="run-b")
    repo2.load()
    entries = repo2.list_credit_ledger_entries()
    assert len(entries) == 1
    assert entries[0].bucket == "hunter_search"


@pytest.mark.parametrize(
    "spent,expected_tier",
    [(0, "ok"), (60, "ok"), (70, "warn"), (85, "degrade"), (95, "hard_stop"), (100, "hard_stop")],
)
def test_status_tier_thresholds(spent, expected_tier):
    ledger = _ledger(cap=100)
    if spent:
        ledger.reserve(bucket="apollo_lead_credit", endpoint="e", credits=spent)
    assert ledger.status("apollo_lead_credit").tier == expected_tier


def test_can_spend_false_at_hard_stop():
    ledger = _ledger(cap=100)
    ledger.reserve(bucket="apollo_lead_credit", endpoint="e", credits=96)
    assert ledger.can_spend("apollo_lead_credit") is False


def test_live_remaining_zero_forces_hard_stop_even_below_local_cap():
    ledger = _ledger(cap=1000)  # local pct tiny, but provider says we're out
    ledger.reserve(bucket="apollo_lead_credit", endpoint="e", credits=1)
    ledger.set_live_remaining("apollo_lead_credit", 0)
    assert ledger.status("apollo_lead_credit").tier == "hard_stop"


def test_path_b_degraded_true_when_apollo_crosses_degrade_threshold():
    ledger = _ledger(cap=100)
    ledger.reserve(bucket="apollo_lead_credit", endpoint="e", credits=90)
    assert ledger.path_b_degraded() is True


def test_path_b_degraded_false_when_only_zerobounce_is_elevated():
    ledger = _ledger(cap=100)
    ledger.reserve(bucket="zerobounce_verification", endpoint="e", credits=90)
    assert ledger.path_b_degraded() is False


def test_reconcile_within_tolerance():
    ledger = _ledger()
    ledger.reserve(bucket="apollo_lead_credit", endpoint="e", credits=10)
    assert ledger.reconcile("apollo_lead_credit", provider_reported_delta=10) is True
    assert ledger.reconcile("apollo_lead_credit", provider_reported_delta=11) is True


def test_reconcile_outside_tolerance():
    ledger = _ledger()
    ledger.reserve(bucket="apollo_lead_credit", endpoint="e", credits=10)
    assert ledger.reconcile("apollo_lead_credit", provider_reported_delta=50) is False


def test_check_and_alert_notifies_only_at_or_above_warn():
    ledger = _ledger(cap=100)
    ledger.reserve(bucket="apollo_lead_credit", endpoint="e", credits=75)
    messages: list[str] = []
    check_and_alert(ledger, buckets=("apollo_lead_credit", "hunter_search"), notify=messages.append)
    assert len(messages) == 1
    assert "WARN" in messages[0]
    assert "apollo_lead_credit" in messages[0]
