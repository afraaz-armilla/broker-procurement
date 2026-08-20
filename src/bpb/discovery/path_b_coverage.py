"""Path B — the systematic title x city x firm sweep for firms with no public
Path A signal (§5). Targeting (Apollo mixed_people search) is free; only the
selected 2-3 per firm ever reach email resolution, which is where credits get
spent (enrichment/email_resolver.py, called later in the pipeline — this module
only discovers, bands, and selects).
"""

from __future__ import annotations

import logging

from bpb import models
from bpb.clients.apollo import ApolloClient
from bpb.config import Settings
from bpb.ledger.credits import CreditLedger
from bpb.selection import role_priority, shortlist
from bpb.store.repo import Repo

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    return " ".join(name.lower().split())


def find_existing_prospect(repo: Repo, firm_id: str, full_name: str) -> models.Prospect | None:
    target = _normalize_name(full_name)
    return next(
        (p for p in repo.list_prospects(firm_id) if _normalize_name(p.full_name) == target), None
    )


def get_or_create_firm(
    repo: Repo, *, name: str, domain: str | None, tier: str | None = None
) -> models.Firm:
    if domain:
        existing = repo.get_firm_by_domain(domain)
        if existing is not None:
            return existing
    return repo.upsert_firm(models.Firm(name=name, domain=domain, tier=tier))


def select_firm(repo: Repo, firm: models.Firm, *, settings: Settings) -> list[models.Prospect]:
    """Band + shortlist every prospect currently on file for this firm — both
    Path A and Path B sourced, since Path A discovery may have surfaced someone
    at the same target firm. Pure selection: does no targeting/discovery of its
    own and does not touch `firm.coverage_state` (callers that actually swept,
    i.e. `sweep_firm`, update that separately). This is what lets a validation
    batch run Path A alone and still get a meaningful selected/reserved split."""
    roles_config = role_priority.load_roles_config()
    all_prospects = repo.list_prospects(firm.id)
    signals_by_prospect = {s.prospect_id: s for s in repo.list_signals(firm.id) if s.prospect_id}
    for prospect in all_prospects:
        band = role_priority.band_prospect(
            prospect, signal=signals_by_prospect.get(prospect.id), roles_config=roles_config
        )
        prospect.role_band = band.band
        prospect.role_score = band.score
        repo.upsert_prospect(prospect)

    result = shortlist.select_for_firm(all_prospects, max_active=settings.roles.max_active_per_firm)
    for p in result.active:
        if p.status == "discovered":
            p.status = "selected"
        repo.upsert_prospect(p)
    for p in result.reserved:
        if p.status != "reserved":
            p.status = "reserved"
        repo.upsert_prospect(p)
    return all_prospects


def sweep_firm(
    repo: Repo,
    firm: models.Firm,
    *,
    apollo: ApolloClient,
    settings: Settings,
    cities: list[str] | None = None,
) -> list[models.Prospect]:
    """Targeting + banding + selection for one firm. Returns every prospect at
    this firm (active + reserved) after selection runs. Idempotent: a person
    already discovered (matched by normalized name) is never duplicated."""
    if not firm.domain:
        logger.warning("Firm %r has no domain configured — skipping Path B sweep", firm.name)
        return repo.list_prospects(firm.id)

    roles_config = role_priority.load_roles_config()
    titles = roles_config.get("decision_maker_seniority", []) + roles_config.get(
        "producer_titles", []
    )
    search_cities = cities if cities is not None else settings.raw_cities.get("cities", [])

    people: list[dict] = []
    for city in search_cities or [None]:
        locations = [city] if city else []
        people.extend(
            apollo.search_people(
                titles=titles, locations=locations, organization_domains=[firm.domain]
            )
        )

    for person in people:
        full_name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
        if not full_name or find_existing_prospect(repo, firm.id, full_name) is not None:
            continue
        repo.upsert_prospect(
            models.Prospect(
                firm_id=firm.id,
                full_name=full_name,
                first_name=person.get("first_name"),
                last_name=person.get("last_name"),
                title=person.get("title"),
                source_path="B",
                linkedin_url=person.get("linkedin_url"),
            )
        )

    all_prospects = select_firm(repo, firm, settings=settings)

    firm.coverage_state = "path_b_swept"
    firm.last_swept_at = models.utcnow()
    repo.upsert_firm(firm)

    return all_prospects


def sweep_all(
    repo: Repo, *, apollo: ApolloClient, ledger: CreditLedger, settings: Settings
) -> dict:
    """Sweep every target firm still `untouched` (i.e. Path A hasn't already
    surfaced someone there this cycle). Skips entirely once the ledger says Path B
    is degraded — per the build plan's ladder, degrade means Path A only, and
    that decision is made once for the whole sweep, not per-firm."""
    if ledger.path_b_degraded():
        logger.warning("Path B degraded (credit budget) — skipping sweep this cycle")
        return {"firms_swept": 0, "skipped_degraded": True}

    firms_cfg = settings.raw_target_firms.get("firms", [])
    firms_swept = 0
    for firm_cfg in firms_cfg:
        if not firm_cfg.get("domain"):
            continue
        firm = get_or_create_firm(
            repo, name=firm_cfg["name"], domain=firm_cfg.get("domain"), tier=firm_cfg.get("tier")
        )
        if firm.coverage_state != "untouched":
            continue
        sweep_firm(repo, firm, apollo=apollo, settings=settings)
        repo.flush()
        firms_swept += 1
        if ledger.path_b_degraded():
            logger.warning("Path B degraded mid-sweep — stopping after %d firms", firms_swept)
            break

    return {
        "firms_swept": firms_swept,
        "prospects_selected": len([p for p in repo.list_prospects() if p.status == "selected"]),
        "prospects_reserved": len([p for p in repo.list_prospects() if p.status == "reserved"]),
    }
