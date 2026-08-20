from datetime import UTC, datetime, timedelta

from bpb import models
from bpb.selection.role_priority import band_prospect

ROLES_CONFIG = {
    "decision_maker_seniority": ["head of", "managing director"],
    "line_of_business_keywords": ["cyber", "e&o", "technology"],
    "producer_titles": ["producer", "broker", "account executive"],
}


def _prospect(title: str) -> models.Prospect:
    return models.Prospect(firm_id="f1", full_name="Jane Doe", title=title)


def test_prospect_with_signal_is_always_band_1_regardless_of_title():
    signal = models.Signal(
        firm_id="f1", url="https://x", url_hash="h", published_at=datetime.now(UTC)
    )
    result = band_prospect(_prospect("Receptionist"), signal=signal, roles_config=ROLES_CONFIG)
    assert result.band == 1
    assert result.score > 100


def test_recent_signal_scores_higher_than_old_signal():
    now = datetime.now(UTC)
    recent = models.Signal(firm_id="f1", url="https://x", url_hash="h1", published_at=now)
    old = models.Signal(
        firm_id="f1", url="https://x", url_hash="h2", published_at=now - timedelta(days=365)
    )
    recent_result = band_prospect(_prospect(""), signal=recent, roles_config=ROLES_CONFIG, now=now)
    old_result = band_prospect(_prospect(""), signal=old, roles_config=ROLES_CONFIG, now=now)
    assert recent_result.score > old_result.score


def test_senior_title_with_lob_keyword_is_band_2():
    result = band_prospect(
        _prospect("Head of Cyber Practice"), signal=None, roles_config=ROLES_CONFIG
    )
    assert result.band == 2


def test_senior_title_without_lob_relevance_is_unbanded():
    result = band_prospect(_prospect("Head of Claims"), signal=None, roles_config=ROLES_CONFIG)
    assert result.band is None


def test_producer_title_is_band_3_even_without_lob_keyword():
    result = band_prospect(_prospect("Account Executive"), signal=None, roles_config=ROLES_CONFIG)
    assert result.band == 3


def test_producer_with_lob_keyword_scores_higher_than_without():
    with_lob = band_prospect(_prospect("Cyber Broker"), signal=None, roles_config=ROLES_CONFIG)
    without_lob = band_prospect(_prospect("Broker"), signal=None, roles_config=ROLES_CONFIG)
    assert with_lob.band == 3
    assert without_lob.band == 3
    assert with_lob.score > without_lob.score


def test_irrelevant_title_is_unbanded():
    result = band_prospect(_prospect("Office Manager"), signal=None, roles_config=ROLES_CONFIG)
    assert result.band is None
