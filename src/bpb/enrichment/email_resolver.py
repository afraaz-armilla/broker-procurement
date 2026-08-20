"""G3: email resolution ladder (§5), cheapest-first — this is where most credit
efficiency is won or lost. Order: known pattern (free) -> Hunter domain-search
(1 credit/hit, also learns the firm's pattern for every future contact there) ->
pattern now known (free) -> Hunter email-finder / Apollo match (1 credit/hit
each). Verification (G4) is a separate, later gate (gates/verification.py) — this
module only resolves a candidate address, it never verifies one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bpb import models
from bpb.clients.apollo import ApolloClient
from bpb.clients.hunter import HunterClient
from bpb.enrichment.pattern_engine import generate_candidate
from bpb.ledger.credits import CreditLedger

logger = logging.getLogger(__name__)


@dataclass
class ResolutionOutcome:
    email: models.Email | None
    firm: models.Firm  # possibly updated in place with a newly learned pattern / catch-all flag


def _split_name(prospect: models.Prospect) -> tuple[str, str]:
    if prospect.first_name and prospect.last_name:
        return prospect.first_name, prospect.last_name
    parts = prospect.full_name.split()
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return prospect.full_name, ""


def _same_person(entry: dict, first: str, last: str) -> bool:
    def norm(s: str | None) -> str:
        return (s or "").strip().lower()

    return norm(entry.get("first_name")) == norm(first) and norm(entry.get("last_name")) == norm(
        last
    )


def resolve_email(
    prospect: models.Prospect,
    firm: models.Firm,
    *,
    hunter: HunterClient,
    apollo: ApolloClient,
    ledger: CreditLedger,
) -> ResolutionOutcome:
    if not firm.domain:
        return ResolutionOutcome(email=None, firm=firm)

    first, last = _split_name(prospect)

    # (a) known pattern, domain not known to be catch-all -> infer, 0 credits.
    # A catch-all domain makes a guessed address unverifiable (every guess looks
    # "valid"), so pattern-based inference is skipped once that's known — see G4's
    # own catch-all handling for why we don't just trust it anyway.
    if firm.email_pattern and not firm.is_catch_all:
        address = generate_candidate(first, last, firm.domain, pattern=firm.email_pattern)
        if address:
            return ResolutionOutcome(
                email=models.Email(
                    prospect_id=prospect.id,
                    address=address,
                    source="inferred",
                    pattern_used=firm.email_pattern,
                ),
                firm=firm,
            )

    # (b) Hunter domain-search — learns the firm's pattern (and catch-all status)
    # for every future contact there, not just this one.
    if ledger.can_spend("hunter_search"):
        ledger.reserve(bucket="hunter_search", endpoint="domain-search", credits=1, firm_id=firm.id)
        result = hunter.domain_search(firm.domain)
        data = result.get("data", {})
        if data.get("accept_all"):
            firm.is_catch_all = True
        pattern = data.get("pattern")
        if pattern:
            firm.email_pattern = pattern
            firm.pattern_source = "hunter"
            firm.pattern_confidence = 1.0

        for entry in data.get("emails") or []:
            if _same_person(entry, first, last):
                return ResolutionOutcome(
                    email=models.Email(
                        prospect_id=prospect.id, address=entry["value"], source="hunter_domain"
                    ),
                    firm=firm,
                )

        # (c) pattern now known (and not catch-all) -> infer, 0 credits.
        if firm.email_pattern and not firm.is_catch_all:
            address = generate_candidate(first, last, firm.domain, pattern=firm.email_pattern)
            if address:
                return ResolutionOutcome(
                    email=models.Email(
                        prospect_id=prospect.id,
                        address=address,
                        source="inferred",
                        pattern_used=firm.email_pattern,
                    ),
                    firm=firm,
                )

    # (d) Hunter email-finder, then Apollo match — last resort, 1 credit each on a hit.
    if ledger.can_spend("hunter_search"):
        ledger.reserve(
            bucket="hunter_search",
            endpoint="email-finder",
            credits=1,
            prospect_id=prospect.id,
            firm_id=firm.id,
        )
        result = hunter.email_finder(domain=firm.domain, first_name=first, last_name=last)
        email = result.get("data", {}).get("email")
        if email:
            return ResolutionOutcome(
                email=models.Email(prospect_id=prospect.id, address=email, source="hunter_finder"),
                firm=firm,
            )

    if ledger.can_spend("apollo_lead_credit"):
        ledger.reserve(
            bucket="apollo_lead_credit",
            endpoint="people/match",
            credits=1,
            prospect_id=prospect.id,
            firm_id=firm.id,
        )
        result = apollo.match_person(first_name=first, last_name=last, domain=firm.domain)
        email = result.get("person", {}).get("email")
        if email:
            return ResolutionOutcome(
                email=models.Email(prospect_id=prospect.id, address=email, source="apollo"),
                firm=firm,
            )

    logger.info("No email resolved for prospect %s at firm %s", prospect.id, firm.id)
    return ResolutionOutcome(email=None, firm=firm)
