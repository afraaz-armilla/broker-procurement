from bpb import models
from bpb.discovery.firm_resolver import resolve_firm


def _firms() -> list[models.Firm]:
    return [
        models.Firm(name="Acme Brokerage LLC", domain="acme.example"),
        models.Firm(name="Beta Insurance Group", domain="beta.example"),
    ]


def test_exact_domain_match_wins_even_with_a_different_name():
    firms = _firms()
    result = resolve_firm("Acme Brokerage (formerly)", "acme.example", firms)
    assert result is not None
    assert result.domain == "acme.example"


def test_fuzzy_name_match_above_threshold():
    firms = _firms()
    result = resolve_firm("Acme Brokerage", None, firms)
    assert result is not None
    assert result.name == "Acme Brokerage LLC"


def test_no_match_returns_none():
    firms = _firms()
    result = resolve_firm("Totally Unrelated Company", None, firms)
    assert result is None


def test_empty_firm_name_returns_none():
    firms = _firms()
    assert resolve_firm("", None, firms) is None


def test_no_target_firms_returns_none():
    assert resolve_firm("Acme Brokerage LLC", "acme.example", []) is None
