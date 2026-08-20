"""The 20-name single-city validation batch (§12) — the source doc's recommended
step 0. Runs the real discover -> select -> resolve email -> verify -> screen
slice and stops before drafting (no LLM cost, no Slack, no hand-off). Reports the
survival funnel Phil needs for a go/no-go, split into email-reachable and
LinkedIn-reachable tracks since §8 decoupled the two channels' gates.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from bpb import models
from bpb.clients.abstract import AbstractEmailClient
from bpb.clients.anthropic_client import AnthropicClient
from bpb.clients.apollo import ApolloClient
from bpb.clients.hunter import HunterClient
from bpb.clients.zerobounce import ZeroBounceClient
from bpb.config import Secrets, load_settings
from bpb.discovery import path_a_signal, path_b_coverage
from bpb.enrichment.email_resolver import resolve_email
from bpb.gates.sanctions.matcher import load_index, screen_name
from bpb.gates.sanctions.refresh import refresh_all
from bpb.gates.verification import verify_email
from bpb.ledger.credits import CreditLedger
from bpb.outreach.linkedin import is_linkedin_eligible
from bpb.store.repo import Repo

logger = logging.getLogger(__name__)


@dataclass
class FirmCoverage:
    firm_name: str
    discovered: int
    selected: int


@dataclass
class BatchStats:
    path_a: dict | None = None
    coverage: list[FirmCoverage] = field(default_factory=list)
    total_selected: int = 0
    batch_size: int = 0
    dropped_over_limit: int = 0
    email_reachable: int = 0
    linkedin_reachable: int = 0
    email_source_breakdown: dict[str, int] = field(default_factory=dict)
    failure_taxonomy: dict[str, int] = field(default_factory=dict)
    credits_spent: dict[str, int] = field(default_factory=dict)

    def _bump(self, d: dict[str, int], key: str) -> None:
        d[key] = d.get(key, 0) + 1


def run_validation_batch(repo: Repo, *, city: str, limit: int, path: str, dry_run: bool) -> str:
    settings = load_settings()
    secrets = Secrets()
    ledger = CreditLedger(repo, settings.budget, run_id=repo.run_id)

    refresh_all(repo, dry_run=dry_run)  # free — guarantees a sanctions index exists
    index = load_index()

    apollo = ApolloClient(secrets.apollo_api_key or "", dry_run=dry_run)
    hunter = HunterClient(secrets.hunter_api_key or "", dry_run=dry_run)
    zb = ZeroBounceClient(secrets.zerobounce_api_key or "", dry_run=dry_run)
    abstract = AbstractEmailClient(secrets.abstract_api_key or "", dry_run=dry_run)

    stats = BatchStats()

    # Path A is a broad content sweep, not city-scoped — run it once up front so
    # any signal it finds at one of the target firms below feeds into that
    # firm's selection (select_firm/sweep_firm re-bands every prospect on file,
    # Path A and Path B sourced alike).
    if path in ("a", "both"):
        llm = AnthropicClient(secrets.anthropic_api_key or "", dry_run=dry_run)
        stats.path_a = path_a_signal.discover_signals(
            repo, settings=settings, llm=llm, dry_run=dry_run
        )

    all_selected: list[models.Prospect] = []
    for firm_cfg in settings.raw_target_firms.get("firms", []):
        if not firm_cfg.get("domain"):
            continue
        firm = path_b_coverage.get_or_create_firm(
            repo, name=firm_cfg["name"], domain=firm_cfg["domain"], tier=firm_cfg.get("tier")
        )
        if path in ("b", "both"):
            prospects = path_b_coverage.sweep_firm(
                repo, firm, apollo=apollo, settings=settings, cities=[city]
            )
        else:  # path == "a": selection only, no Apollo targeting call
            prospects = path_b_coverage.select_firm(repo, firm, settings=settings)
        selected = [p for p in prospects if p.status == "selected"]
        stats.coverage.append(
            FirmCoverage(firm_name=firm.name, discovered=len(prospects), selected=len(selected))
        )
        all_selected.extend(selected)
    repo.flush()

    stats.total_selected = len(all_selected)
    batch = all_selected[:limit]
    stats.batch_size = len(batch)
    stats.dropped_over_limit = max(0, len(all_selected) - limit)
    if stats.dropped_over_limit:
        logger.warning(
            "Validation batch limit=%d reached; %d additional selected prospects were not "
            "run through the full chain this batch (not silently covered — see report).",
            limit,
            stats.dropped_over_limit,
        )

    for prospect in batch:
        prospect_firm = repo.get_firm(prospect.firm_id)
        assert prospect_firm is not None

        verdict = screen_name(
            prospect.full_name,
            index,
            potential_match_threshold=settings.sanctions.potential_match_threshold,
        )
        repo.add_screening(
            models.Screening(
                prospect_id=prospect.id,
                verdict=verdict.verdict,
                best_score=verdict.best_score,
                matched_entry_name=verdict.matched_entry_name,
                matched_lists_json=json.dumps(verdict.matched_lists),
            )
        )
        if verdict.verdict == "match":
            prospect.status = "disqualified_sanctions"
            repo.upsert_prospect(prospect)
            stats._bump(stats.failure_taxonomy, "sanctions_match")
            continue
        if verdict.verdict == "potential_match":
            stats._bump(stats.failure_taxonomy, "sanctions_needs_review")
            continue  # held for human review — not reachable via either channel yet

        if is_linkedin_eligible(prospect):
            stats.linkedin_reachable += 1

        outcome = resolve_email(
            prospect, prospect_firm, hunter=hunter, apollo=apollo, ledger=ledger
        )
        repo.upsert_firm(outcome.firm)
        if outcome.email is None:
            stats._bump(stats.failure_taxonomy, "no_email_found")
            continue
        repo.add_email(outcome.email)

        verification = verify_email(
            outcome.email,
            zb_client=zb,
            abstract_client=abstract,
            allow_catch_all=settings.policy.allow_catch_all_emails,
        )
        for v in verification.all_verifications:
            repo.add_verification(v)

        if verification.passed:
            stats.email_reachable += 1
            stats._bump(stats.email_source_breakdown, outcome.email.source)
        elif verification.hold:
            stats._bump(stats.failure_taxonomy, "catch_all_or_unknown")
        else:
            stats._bump(stats.failure_taxonomy, "invalid_email")

    repo.flush()

    budget_buckets: tuple[models.CreditBucket, ...] = (
        "apollo_lead_credit",
        "hunter_search",
        "zerobounce_verification",
        "abstract_verification",
    )
    for bucket in budget_buckets:
        stats.credits_spent[bucket] = ledger.local_spent(bucket)

    return _format_report(city, limit, stats)


def _format_report(city: str, limit: int, s: BatchStats) -> str:
    n = s.batch_size
    email_pct = (s.email_reachable / n * 100) if n else 0.0
    linkedin_pct = (s.linkedin_reachable / n * 100) if n else 0.0
    lines = [
        f"Validation batch — city={city!r} limit={limit}",
        f"Selected across target firms: {s.total_selected} "
        f"(ran full chain on {n}, dropped {s.dropped_over_limit} over the limit)",
        "",
        *([f"Path A discovery: {s.path_a}", ""] if s.path_a is not None else []),
        "Per-firm coverage:",
        *[f"  {c.firm_name}: discovered={c.discovered} selected={c.selected}" for c in s.coverage],
        "",
        f"Email-reachable rate:    {s.email_reachable}/{n} ({email_pct:.0f}%)",
        f"LinkedIn-reachable rate: {s.linkedin_reachable}/{n} ({linkedin_pct:.0f}%)",
        f"Email source breakdown: {s.email_source_breakdown}",
        f"Failure taxonomy: {s.failure_taxonomy}",
        "",
        "Credits spent this batch:",
        *[f"  {bucket}: {spent}" for bucket, spent in s.credits_spent.items()],
    ]
    return "\n".join(lines)
