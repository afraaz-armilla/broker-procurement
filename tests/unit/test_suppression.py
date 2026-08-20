from bpb import models
from bpb.gates import suppression
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend


def _repo():
    backend = MemoryBackend()
    return bootstrap(backend, run_id="test-run")


def test_normalize_email_lowercases_and_strips():
    assert suppression.normalize_email("  Jane.Doe@Example.com ") == "jane.doe@example.com"


def test_normalize_person_key_is_case_and_whitespace_insensitive():
    a = suppression.normalize_person_key("firm-1", "  Jane   Doe ")
    b = suppression.normalize_person_key("firm-1", "jane doe")
    assert a == b


def test_prospect_not_suppressed_by_default():
    repo = _repo()
    prospect = models.Prospect(firm_id="f1", full_name="Jane Doe")
    assert suppression.is_prospect_suppressed(repo, prospect) is False


def test_suppress_person_then_check():
    repo = _repo()
    prospect = models.Prospect(firm_id="f1", full_name="Jane Doe")
    suppression.suppress_person(repo, prospect, reason="already_contacted", source="test")
    assert suppression.is_prospect_suppressed(repo, prospect) is True


def test_suppress_email_then_check():
    repo = _repo()
    suppression.suppress_email(repo, "Jane@Example.com", reason="bounced", source="test")
    assert suppression.is_email_suppressed(repo, "jane@example.com") is True


def test_different_person_at_different_firm_not_suppressed():
    repo = _repo()
    p1 = models.Prospect(firm_id="f1", full_name="Jane Doe")
    p2 = models.Prospect(firm_id="f2", full_name="Jane Doe")
    suppression.suppress_person(repo, p1, reason="manual", source="test")
    assert suppression.is_prospect_suppressed(repo, p2) is False


def test_released_suppression_no_longer_blocks():
    repo = _repo()
    prospect = models.Prospect(firm_id="f1", full_name="Jane Doe")
    entry = suppression.suppress_person(repo, prospect, reason="manual", source="test")
    entry.released_at = models.utcnow()
    repo.upsert_suppression(entry)
    assert suppression.is_prospect_suppressed(repo, prospect) is False
