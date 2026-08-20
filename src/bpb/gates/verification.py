"""G4: email verification cascade — ZeroBounce primary, Abstract fallback only when
ZeroBounce is inconclusive (`unknown`). This module only calls the two clients and
decides pass/hold/fail; it does NOT touch the credit ledger (see ledger/credits.py)
— callers reserve budget before calling `verify_email` and persist the returned
Verification row(s) via repo.add_verification().
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from bpb import models
from bpb.clients.abstract import AbstractEmailClient
from bpb.clients.zerobounce import ZeroBounceClient

HARD_FAIL_STATUSES = {"invalid", "spamtrap", "abuse", "do_not_mail"}


@dataclass
class VerificationOutcome:
    passed: bool  # True => proceed to drafting
    hold: bool  # True => needs_manual (not a hard fail, not yet a pass)
    verification: models.Verification
    fallback_verification: models.Verification | None = None

    @property
    def all_verifications(self) -> list[models.Verification]:
        out = [self.verification]
        if self.fallback_verification:
            out.append(self.fallback_verification)
        return out


def verify_email(
    email: models.Email,
    *,
    zb_client: ZeroBounceClient,
    abstract_client: AbstractEmailClient,
    allow_catch_all: bool = False,
) -> VerificationOutcome:
    zb_result = zb_client.validate(email.address)
    status = zb_result.get("status", "unknown")
    verification = models.Verification(
        email_id=email.id,
        provider="zerobounce",
        status=status,
        sub_status=zb_result.get("sub_status") or None,
        raw_json=json.dumps(zb_result),
        credits_charged=zb_client.credits_charged_for(zb_result),
    )

    if status == "valid":
        return VerificationOutcome(passed=True, hold=False, verification=verification)
    if status in HARD_FAIL_STATUSES:
        return VerificationOutcome(passed=False, hold=False, verification=verification)
    if status == "catch-all":
        return VerificationOutcome(
            passed=allow_catch_all, hold=not allow_catch_all, verification=verification
        )

    # status == "unknown" -> fall back to Abstract to try to disambiguate.
    ab_result = abstract_client.validate(email.address)
    deliverability = ab_result.get("deliverability", "UNKNOWN")
    fallback = models.Verification(
        email_id=email.id,
        provider="abstract",
        status=deliverability,
        raw_json=json.dumps(ab_result),
        score=_safe_float(ab_result.get("quality_score")),
        credits_charged=1,
    )
    if deliverability == "DELIVERABLE":
        return VerificationOutcome(
            passed=True, hold=False, verification=verification, fallback_verification=fallback
        )
    if deliverability == "UNDELIVERABLE":
        return VerificationOutcome(
            passed=False, hold=False, verification=verification, fallback_verification=fallback
        )
    return VerificationOutcome(
        passed=False, hold=True, verification=verification, fallback_verification=fallback
    )


def _safe_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
