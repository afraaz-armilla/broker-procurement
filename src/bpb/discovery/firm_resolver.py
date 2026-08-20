"""Resolve an extracted firm name/domain to a known target-firm record (§4). No
match -> the caller creates an `on_target_list=false` firm and surfaces it in the
run report rather than silently prospecting an off-list firm.
"""

from __future__ import annotations

from rapidfuzz import fuzz, process

from bpb import models
from bpb.gates.sanctions.matcher import normalize_name

MATCH_THRESHOLD = 88


def resolve_firm(
    firm_name: str,
    domain_guess: str | None,
    target_firms: list[models.Firm],
    *,
    threshold: int = MATCH_THRESHOLD,
) -> models.Firm | None:
    if domain_guess:
        exact = next(
            (f for f in target_firms if f.domain and f.domain.lower() == domain_guess.lower()), None
        )
        if exact is not None:
            return exact

    if not firm_name or not target_firms:
        return None

    names = [normalize_name(f.name) for f in target_firms]
    match = process.extractOne(normalize_name(firm_name), names, scorer=fuzz.token_set_ratio)
    if match is None:
        return None
    _, score, idx = match
    return target_firms[idx] if score >= threshold else None
