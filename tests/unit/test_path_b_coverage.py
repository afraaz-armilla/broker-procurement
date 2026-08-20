from bpb.clients.apollo import ApolloClient
from bpb.config import load_settings
from bpb.discovery.path_b_coverage import get_or_create_firm, sweep_all, sweep_firm
from bpb.ledger.credits import CreditLedger
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend


def _repo():
    backend = MemoryBackend()
    return bootstrap(backend, run_id="test-run")


def test_sweep_firm_discovers_and_bands_a_prospect():
    repo = _repo()
    settings = load_settings()
    apollo = ApolloClient("fake", dry_run=True)
    firm = get_or_create_firm(repo, name="Acme Brokerage", domain="acme.example")

    prospects = sweep_firm(repo, firm, apollo=apollo, settings=settings)

    assert len(prospects) == 1
    p = prospects[0]
    assert p.full_name == "Fixture Broker"
    assert p.source_path == "B"
    assert p.role_band == 3  # "Vice President" matches producer_titles
    assert p.status == "selected"  # only one candidate -> selected, not reserved


def test_sweep_firm_is_idempotent_across_repeated_runs():
    repo = _repo()
    settings = load_settings()
    apollo = ApolloClient("fake", dry_run=True)
    firm = get_or_create_firm(repo, name="Acme Brokerage", domain="acme.example")

    sweep_firm(repo, firm, apollo=apollo, settings=settings)
    sweep_firm(repo, firm, apollo=apollo, settings=settings)

    assert len(repo.list_prospects(firm.id)) == 1


def test_sweep_firm_marks_firm_as_swept():
    repo = _repo()
    settings = load_settings()
    apollo = ApolloClient("fake", dry_run=True)
    firm = get_or_create_firm(repo, name="Acme Brokerage", domain="acme.example")

    sweep_firm(repo, firm, apollo=apollo, settings=settings)

    reloaded = repo.get_firm(firm.id)
    assert reloaded.coverage_state == "path_b_swept"
    assert reloaded.last_swept_at is not None


def test_sweep_firm_without_domain_is_a_no_op():
    repo = _repo()
    settings = load_settings()
    apollo = ApolloClient("fake", dry_run=True)
    firm = get_or_create_firm(repo, name="No Domain Brokerage", domain=None)

    prospects = sweep_firm(repo, firm, apollo=apollo, settings=settings)

    assert prospects == []


def test_get_or_create_firm_reuses_existing_by_domain():
    repo = _repo()
    first = get_or_create_firm(repo, name="Acme Brokerage", domain="acme.example")
    repo.flush()
    second = get_or_create_firm(
        repo, name="Acme Brokerage (different casing of name)", domain="acme.example"
    )
    assert first.id == second.id


def test_sweep_all_skips_entirely_when_path_b_degraded():
    from tests.unit.test_credits import _settings as budget_settings

    repo = _repo()
    settings = load_settings()
    apollo = ApolloClient("fake", dry_run=True)
    ledger = CreditLedger(repo, budget_settings(cap=100), run_id="test-run")
    ledger.reserve(bucket="apollo_lead_credit", endpoint="preexisting", credits=90)

    stats = sweep_all(repo, apollo=apollo, ledger=ledger, settings=settings)

    assert stats.get("skipped_degraded") is True
    assert repo.list_firms() == []


def test_sweep_all_processes_configured_target_firms():
    from tests.unit.test_credits import _settings as budget_settings

    repo = _repo()
    settings = load_settings()
    apollo = ApolloClient("fake", dry_run=True)
    ledger = CreditLedger(repo, budget_settings(cap=100), run_id="test-run")

    stats = sweep_all(repo, apollo=apollo, ledger=ledger, settings=settings)

    # TEMPLATE_target_firms.yaml ships with one example firm that has a domain.
    assert stats["firms_swept"] == 1
    assert len(repo.list_firms()) == 1
