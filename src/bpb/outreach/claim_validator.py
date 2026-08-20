"""Pure anti-hallucination check on a drafted outreach message (§8) — the guard
against a hallucinated "loved your piece in The Insurer" that never existed, the
single highest reputation-risk failure mode in the system.

Two concrete, checkable rules rather than general-purpose NER:
1. Any quoted substring in the draft must appear verbatim in the linked signal's
   evidence_quote — if there's no signal at all, the draft must not quote
   anything (nothing to quote).
2. Any named trade-press publication the draft mentions must be the one the
   signal actually came from, not a different (fabricated) one.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from bpb import models

_QUOTE_PATTERN = re.compile(r'"([^"]{4,})"|“([^”]{4,})”')


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _extract_quoted_substrings(text: str) -> list[str]:
    matches = _QUOTE_PATTERN.findall(text)
    return [a or b for a, b in matches]


@dataclass(frozen=True)
class ClaimViolation:
    kind: str
    detail: str


@dataclass(frozen=True)
class ClaimValidationResult:
    passed: bool
    violations: list[ClaimViolation] = field(default_factory=list)


def validate_claims(
    *,
    draft_subject: str,
    draft_body: str,
    signal: models.Signal | None,
    known_publications: list[str],
) -> ClaimValidationResult:
    text = f"{draft_subject}\n{draft_body}"
    violations: list[ClaimViolation] = []

    quoted = _extract_quoted_substrings(text)
    if signal is None or not signal.evidence_quote:
        for q in quoted:
            violations.append(
                ClaimViolation(
                    "quote_without_signal",
                    f"Draft quotes {q!r} but no signal/evidence is attached to this prospect",
                )
            )
    else:
        evidence_norm = _normalize(signal.evidence_quote)
        for q in quoted:
            if _normalize(q) not in evidence_norm:
                violations.append(
                    ClaimViolation(
                        "quote_not_in_evidence",
                        f"Quoted text {q!r} does not appear verbatim in the signal's evidence "
                        "quote",
                    )
                )

    correct_publication = _normalize(signal.publication) if signal and signal.publication else None
    text_norm = _normalize(text)
    for pub in known_publications:
        pub_norm = _normalize(pub)
        if pub_norm in text_norm and pub_norm != correct_publication:
            violations.append(
                ClaimViolation(
                    "wrong_publication",
                    f"Draft references {pub!r}, but the linked signal's publication is "
                    f"{signal.publication if signal else None!r}",
                )
            )

    return ClaimValidationResult(passed=not violations, violations=violations)
