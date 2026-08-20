"""G0: suppression/dedup — the cheapest gate, checked first, and the safety net
that stops a contacted broker from ever being re-surfaced. Key normalization is
pure and testable in isolation; the repo-touching check/suppress wrappers are
thin convenience layers over `Repo.is_suppressed`/`upsert_suppression`.
"""

from __future__ import annotations

from bpb import models
from bpb.store.repo import Repo


def normalize_email(address: str) -> str:
    return address.strip().lower()


def normalize_person_key(firm_id: str, full_name: str) -> str:
    return f"{firm_id}:{' '.join(full_name.lower().split())}"


def is_prospect_suppressed(repo: Repo, prospect: models.Prospect) -> bool:
    return repo.is_suppressed("person", normalize_person_key(prospect.firm_id, prospect.full_name))


def is_email_suppressed(repo: Repo, address: str) -> bool:
    return repo.is_suppressed("email", normalize_email(address))


def suppress_person(
    repo: Repo, prospect: models.Prospect, *, reason: models.SuppressionReason, source: str
) -> models.Suppression:
    key = normalize_person_key(prospect.firm_id, prospect.full_name)
    return repo.upsert_suppression(
        models.Suppression(scope="person", key_normalized=key, reason=reason, source=source)
    )


def suppress_email(
    repo: Repo, address: str, *, reason: models.SuppressionReason, source: str
) -> models.Suppression:
    return repo.upsert_suppression(
        models.Suppression(
            scope="email", key_normalized=normalize_email(address), reason=reason, source=source
        )
    )
