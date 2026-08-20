"""Pure email-pattern inference and learning (§5). No I/O — generates a candidate
address from a known pattern, or learns a firm's pattern from a handful of
already-verified addresses at that domain.
"""

from __future__ import annotations

import re
from collections import Counter

# Ordered candidate templates, most-common-in-practice first. `learn_pattern`
# stops at the first template that reproduces a real address, so this order also
# controls tie-breaking when a domain's few known addresses are ambiguous.
CANDIDATE_TEMPLATES = [
    "{first}.{last}",
    "{first}{last}",
    "{f}{last}",
    "{first}_{last}",
    "{first}",
    "{f}.{last}",
]


def _clean(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def render_pattern(template: str, first_name: str, last_name: str) -> str:
    first, last = _clean(first_name), _clean(last_name)
    f = first[:1]
    return template.format(first=first, last=last, f=f)


def generate_candidate(
    first_name: str, last_name: str, domain: str, *, pattern: str
) -> str | None:
    """One candidate address from a KNOWN pattern (Hunter-reported, learned, or a
    config override) — see enrichment/email_resolver.py's ladder step (c). This
    module never guesses blindly across CANDIDATE_TEMPLATES for a single contact;
    that would burn a verification credit per guess for little gain. Blind
    guessing across templates only happens in `learn_pattern`, over already-free,
    already-verified addresses.
    """
    local = render_pattern(pattern, first_name, last_name)
    if not local:
        return None
    return f"{local}@{domain}"


def learn_pattern(verified_emails: list[tuple[str, str, str]], *, min_votes: int = 2) -> str | None:
    """`verified_emails`: (first_name, last_name, address) tuples for addresses
    already confirmed valid at one domain (e.g. accumulated across prior runs).
    Returns the template most of them agree with, if at least `min_votes` do."""
    votes: Counter[str] = Counter()
    for first_name, last_name, address in verified_emails:
        local = address.split("@")[0].lower()
        for template in CANDIDATE_TEMPLATES:
            if render_pattern(template, first_name, last_name) == local:
                votes[template] += 1
                break
    if not votes:
        return None
    template, count = votes.most_common(1)[0]
    return template if count >= min_votes else None
