from bpb import models
from bpb.clients.apollo import ApolloClient
from bpb.clients.hunter import HunterClient
from bpb.enrichment.email_resolver import resolve_email
from bpb.ledger.credits import CreditLedger
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend
from tests.unit.test_credits import _settings


def _ledger() -> CreditLedger:
    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="test-run")
    return CreditLedger(repo, _settings(cap=100), run_id="test-run")


def _firm(**kwargs) -> models.Firm:
    kwargs.setdefault("domain", "acme.example")
    return models.Firm(name="Acme Brokerage", **kwargs)


def _prospect(full_name: str, first: str | None = None, last: str | None = None) -> models.Prospect:
    return models.Prospect(firm_id="f1", full_name=full_name, first_name=first, last_name=last)


def _clients():
    return HunterClient("fake", dry_run=True), ApolloClient("fake", dry_run=True)


def test_known_pattern_resolves_without_spending_any_credits():
    hunter, apollo = _clients()
    ledger = _ledger()
    firm = _firm(email_pattern="{first}.{last}")
    prospect = _prospect("Jane Doe")

    outcome = resolve_email(prospect, firm, hunter=hunter, apollo=apollo, ledger=ledger)

    assert outcome.email is not None
    assert outcome.email.address == "jane.doe@acme.example"
    assert outcome.email.source == "inferred"
    assert ledger.local_spent("hunter_search") == 0
    assert ledger.local_spent("apollo_lead_credit") == 0


def test_catch_all_firm_skips_pattern_inference_even_with_known_pattern():
    hunter, apollo = _clients()
    ledger = _ledger()
    firm = _firm(email_pattern="{first}.{last}", is_catch_all=True)
    prospect = _prospect("Jane Doe")

    outcome = resolve_email(prospect, firm, hunter=hunter, apollo=apollo, ledger=ledger)

    # Falls through to Hunter domain-search (dry-run fixture doesn't match "Jane
    # Doe") then email-finder, which the fixture DOES resolve.
    assert outcome.email is not None
    assert outcome.email.source == "hunter_finder"


def test_no_known_pattern_direct_hunter_domain_hit_for_matching_name():
    hunter, apollo = _clients()
    ledger = _ledger()
    firm = _firm()  # no pattern yet
    prospect = _prospect("Fixture Broker", "Fixture", "Broker")  # matches the dry-run fixture

    outcome = resolve_email(prospect, firm, hunter=hunter, apollo=apollo, ledger=ledger)

    assert outcome.email is not None
    assert outcome.email.source == "hunter_domain"
    assert ledger.local_spent("hunter_search") == 1
    # The firm's pattern is now cached for every future contact there.
    assert firm.email_pattern == "{first}.{last}"


def test_no_known_pattern_no_direct_hit_falls_through_to_inferred_with_learned_pattern():
    hunter, apollo = _clients()
    ledger = _ledger()
    firm = _firm()
    prospect = _prospect("Jane Doe")  # doesn't match the fixture's "Fixture Broker"

    outcome = resolve_email(prospect, firm, hunter=hunter, apollo=apollo, ledger=ledger)

    assert outcome.email is not None
    assert outcome.email.source == "inferred"
    assert outcome.email.address == "jane.doe@acme.example"
    assert firm.email_pattern == "{first}.{last}"  # learned as a side effect


def test_no_domain_returns_none_without_any_calls():
    hunter, apollo = _clients()
    ledger = _ledger()
    firm = _firm(domain=None)
    prospect = _prospect("Jane Doe")

    outcome = resolve_email(prospect, firm, hunter=hunter, apollo=apollo, ledger=ledger)

    assert outcome.email is None
    assert ledger.local_spent("hunter_search") == 0


def test_budget_exhausted_skips_hunter_goes_straight_to_apollo():
    hunter, apollo = _clients()
    ledger = _ledger()
    ledger.reserve(bucket="hunter_search", endpoint="preexisting", credits=100)  # exhausts hunter
    firm = _firm()
    prospect = _prospect("Jane Doe")

    outcome = resolve_email(prospect, firm, hunter=hunter, apollo=apollo, ledger=ledger)

    assert outcome.email is not None
    assert outcome.email.source == "apollo"
    assert ledger.local_spent("apollo_lead_credit") == 1


def test_all_budgets_exhausted_returns_none():
    hunter, apollo = _clients()
    ledger = _ledger()
    ledger.reserve(bucket="hunter_search", endpoint="preexisting", credits=100)
    ledger.reserve(bucket="apollo_lead_credit", endpoint="preexisting", credits=100)
    firm = _firm()
    prospect = _prospect("Jane Doe")

    outcome = resolve_email(prospect, firm, hunter=hunter, apollo=apollo, ledger=ledger)

    assert outcome.email is None
