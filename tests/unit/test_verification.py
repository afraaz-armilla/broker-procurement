"""Pure tests of the ZeroBounce->Abstract cascade decision logic in
gates/verification.py, using lightweight fakes (same shape as the real clients)
so no network or dry-run fixture machinery is involved — just the branching."""

from bpb import models
from bpb.gates.verification import verify_email


class FakeZeroBounce:
    def __init__(self, status: str, sub_status: str = ""):
        self.status = status
        self.sub_status = sub_status

    def validate(self, email: str) -> dict:
        return {"address": email, "status": self.status, "sub_status": self.sub_status}

    def credits_charged_for(self, result: dict) -> int:
        return 0 if result["status"] == "unknown" else 1


class FakeAbstract:
    def __init__(self, deliverability: str):
        self.deliverability = deliverability

    def validate(self, email: str) -> dict:
        return {"email": email, "deliverability": self.deliverability, "quality_score": "0.5"}


def _email() -> models.Email:
    return models.Email(prospect_id="p1", address="jane@example.com", source="hunter_domain")


def _verify(zb_status: str, ab_deliverability: str = "DELIVERABLE", **kwargs):
    return verify_email(
        _email(),
        zb_client=FakeZeroBounce(zb_status),
        abstract_client=FakeAbstract(ab_deliverability),
        **kwargs,
    )


def test_valid_passes_without_abstract_fallback():
    outcome = _verify("valid")
    assert outcome.passed is True
    assert outcome.hold is False
    assert outcome.fallback_verification is None
    assert outcome.verification.credits_charged == 1


def test_invalid_hard_fails():
    outcome = _verify("invalid")
    assert outcome.passed is False
    assert outcome.hold is False


def test_spamtrap_hard_fails():
    outcome = _verify("spamtrap")
    assert outcome.passed is False
    assert outcome.hold is False


def test_catch_all_holds_by_default():
    outcome = _verify("catch-all", allow_catch_all=False)
    assert outcome.passed is False
    assert outcome.hold is True


def test_catch_all_passes_when_explicitly_allowed():
    outcome = _verify("catch-all", allow_catch_all=True)
    assert outcome.passed is True
    assert outcome.hold is False


def test_unknown_falls_back_to_abstract_deliverable_passes():
    outcome = _verify("unknown", "DELIVERABLE")
    assert outcome.passed is True
    assert outcome.fallback_verification is not None
    assert outcome.fallback_verification.provider == "abstract"
    assert outcome.verification.credits_charged == 0  # ZeroBounce doesn't charge for unknown


def test_unknown_falls_back_to_abstract_undeliverable_fails():
    outcome = _verify("unknown", "UNDELIVERABLE")
    assert outcome.passed is False
    assert outcome.hold is False


def test_unknown_stays_unknown_after_fallback_holds():
    outcome = _verify("unknown", "UNKNOWN")
    assert outcome.passed is False
    assert outcome.hold is True


def test_all_verifications_includes_fallback_when_present():
    outcome = _verify("unknown", "DELIVERABLE")
    assert len(outcome.all_verifications) == 2
    outcome2 = _verify("valid", "DELIVERABLE")
    assert len(outcome2.all_verifications) == 1
