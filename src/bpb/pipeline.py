"""Pipeline orchestration — wires the stage modules together. Filled in across
phases 4 (Path B), 6 (Path A), 7 (drafting/assemble), 8 (approval), 9 (hand-off).
Each `run_*` function is what a CLI command / scheduled workflow calls; each is
threaded the same `dry_run` flag, enforced at the HTTP boundary in clients/base.py
so a stage never needs its own dry-run branching.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from bpb import models
from bpb.store.repo import Repo


def run_discover(
    repo: Repo, *, path: str, firm_domain: str | None, city: str | None, dry_run: bool
) -> dict:
    from bpb.clients.anthropic_client import AnthropicClient
    from bpb.clients.apollo import ApolloClient
    from bpb.config import Secrets, load_settings
    from bpb.discovery import path_a_signal, path_b_coverage
    from bpb.ledger.credits import CreditLedger

    settings = load_settings()
    secrets = Secrets()
    results: dict = {}

    # Path A is a broad content sweep, not scoped to one firm/city, so a --firm
    # filter (a Path-B-specific narrow mode) skips it rather than trying to apply.
    if path in ("a", "both") and not firm_domain:
        llm = AnthropicClient(secrets.anthropic_api_key or "", dry_run=dry_run)
        results["path_a"] = path_a_signal.discover_signals(
            repo, settings=settings, llm=llm, dry_run=dry_run
        )

    if path in ("b", "both"):
        ledger = CreditLedger(repo, settings.budget, run_id=repo.run_id)
        apollo = ApolloClient(secrets.apollo_api_key or "", dry_run=dry_run)
        if firm_domain:
            firm = path_b_coverage.get_or_create_firm(repo, name=firm_domain, domain=firm_domain)
            prospects = path_b_coverage.sweep_firm(
                repo, firm, apollo=apollo, settings=settings, cities=[city] if city else None
            )
            repo.flush()
            results["path_b"] = {"firm": firm_domain, "prospects": len(prospects)}
        else:
            results["path_b"] = path_b_coverage.sweep_all(
                repo, apollo=apollo, ledger=ledger, settings=settings
            )

    return results


def _bump(d: dict, key: str) -> None:
    d[key] = d.get(key, 0) + 1


def _next_row_ref(repo: Repo) -> str:
    week = datetime.now(UTC).strftime("%YW%V")
    n = len(repo.list_queue_items()) + 1
    return f"Q-{week}-{n:03d}"


def run_assemble(repo: Repo, *, dry_run: bool) -> dict:
    """Promote reserves -> gate cascade (G0 suppression, G2 sanctions, G3 email
    resolution, G4 verification, G5 draft+claim validation) -> QueueItem rows.
    G1 (fit/role priority) already happened at discovery/selection time. G6
    (human approval) is phase 8 — this stops at a populated, unapproved queue.
    """
    from bpb.clients.abstract import AbstractEmailClient
    from bpb.clients.anthropic_client import AnthropicClient
    from bpb.clients.apollo import ApolloClient
    from bpb.clients.hunter import HunterClient
    from bpb.clients.zerobounce import ZeroBounceClient
    from bpb.config import Secrets, load_settings
    from bpb.enrichment.email_resolver import resolve_email
    from bpb.gates import suppression as suppression_gate
    from bpb.gates.sanctions.matcher import load_index, screen_name
    from bpb.gates.sanctions.refresh import refresh_all
    from bpb.gates.verification import verify_email
    from bpb.ledger.credits import CreditLedger
    from bpb.outreach.drafter import draft_outreach
    from bpb.outreach.linkedin import build_linkedin_payload, is_linkedin_eligible
    from bpb.selection.shortlist import promote_reserves

    settings = load_settings()
    secrets = Secrets()
    ledger = CreditLedger(repo, settings.budget, run_id=repo.run_id)

    refresh_all(repo, dry_run=dry_run)  # free — guarantees a current sanctions index
    index = load_index()

    apollo = ApolloClient(secrets.apollo_api_key or "", dry_run=dry_run)
    hunter = HunterClient(secrets.hunter_api_key or "", dry_run=dry_run)
    zb = ZeroBounceClient(secrets.zerobounce_api_key or "", dry_run=dry_run)
    abstract = AbstractEmailClient(secrets.abstract_api_key or "", dry_run=dry_run)
    llm = AnthropicClient(secrets.anthropic_api_key or "", dry_run=dry_run)

    stats: dict = {"promoted": 0, "queue_items_created": 0, "needs_manual": 0, "disqualified": {}}

    for firm in repo.list_firms():
        prospects = repo.list_prospects(firm.id)
        active = [p for p in prospects if p.status == "selected"]
        reserved = [p for p in prospects if p.status == "reserved"]
        promoted = promote_reserves(
            active,
            reserved,
            max_active=settings.roles.max_active_per_firm,
            stale_after_days=settings.roles.stale_after_days,
        )
        for p in promoted:
            p.status = "selected"
            repo.upsert_prospect(p)
            stats["promoted"] += 1
    if stats["promoted"]:
        repo.flush()

    known_publications = [f["name"] for f in settings.raw_sources.get("trade_press", [])]
    policy = settings.policy
    sequence_variants = settings.raw_message_sequence.get("variants", {})
    sender_name = "Phil"

    for prospect in [p for p in repo.list_prospects() if p.status == "selected"]:
        prospect_firm = repo.get_firm(prospect.firm_id)
        assert prospect_firm is not None

        if suppression_gate.is_prospect_suppressed(repo, prospect):
            prospect.status = "suppressed"
            repo.upsert_prospect(prospect)
            _bump(stats["disqualified"], "suppressed")
            continue

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
            suppression_gate.suppress_person(
                repo, prospect, reason="sanctions_hit", source="sanctions_screen"
            )
            _bump(stats["disqualified"], "sanctions_match")
            continue
        if verdict.verdict == "potential_match":
            prospect.status = "needs_manual"
            repo.upsert_prospect(prospect)
            _bump(stats["disqualified"], "sanctions_needs_review")
            continue

        signal = next(
            (s for s in repo.list_signals(prospect_firm.id) if s.prospect_id == prospect.id), None
        )
        linkedin_eligible = is_linkedin_eligible(prospect)

        email_verified: models.Email | None = None
        outcome = resolve_email(
            prospect, prospect_firm, hunter=hunter, apollo=apollo, ledger=ledger
        )
        repo.upsert_firm(outcome.firm)
        if outcome.email is not None and not suppression_gate.is_email_suppressed(
            repo, outcome.email.address
        ):
            repo.add_email(outcome.email)
            verification = verify_email(
                outcome.email,
                zb_client=zb,
                abstract_client=abstract,
                allow_catch_all=policy.allow_catch_all_emails,
            )
            for v in verification.all_verifications:
                repo.add_verification(v)
            if verification.passed:
                email_verified = outcome.email
            elif not verification.hold:
                suppression_gate.suppress_email(
                    repo, outcome.email.address, reason="bounced", source="zerobounce"
                )

        if email_verified is None and not linkedin_eligible:
            prospect.status = "disqualified_email"
            repo.upsert_prospect(prospect)
            _bump(stats["disqualified"], "no_reachable_channel")
            continue

        variant_key = (
            "path_a_warm"
            if prospect.source_path == "A"
            else ("path_b_soft" if policy.b_only_policy == "soft" else "path_b_hold")
        )
        variant = sequence_variants.get(variant_key, {})
        if variant.get("send") is False:
            # B-only "hold" policy: stays `selected`, no queue item this cycle —
            # a future Path A signal re-bands them to Band 1 and revisits this.
            _bump(stats["disqualified"], "b_only_hold")
            continue

        draft = draft_outreach(
            prospect, prospect_firm, signal, llm=llm, settings=settings, template_variant=variant,
            sender_name=sender_name, known_publications=known_publications, dry_run=dry_run,
        )
        if draft.needs_manual:
            prospect.status = "needs_manual"
            repo.upsert_prospect(prospect)
            stats["needs_manual"] += 1
            continue

        prospect.status = "drafted"
        repo.upsert_prospect(prospect)

        if email_verified is not None:
            repo.upsert_queue_item(
                models.QueueItem(
                    prospect_id=prospect.id, channel="email", row_ref=_next_row_ref(repo),
                    draft_subject=draft.subject, draft_body=draft.body or "",
                    signal_id=signal.id if signal else None,
                )
            )
            stats["queue_items_created"] += 1

        if linkedin_eligible and draft.linkedin_message:
            repo.upsert_queue_item(
                models.QueueItem(
                    prospect_id=prospect.id, channel="linkedin", row_ref=_next_row_ref(repo),
                    draft_body=build_linkedin_payload(
                        draft.linkedin_message, prospect.linkedin_url or ""
                    ),
                    signal_id=signal.id if signal else None,
                )
            )
            stats["queue_items_created"] += 1

    repo.flush()

    from bpb.approval.publisher import publish_pending
    from bpb.clients.slack import SlackClient

    if settings.policy.slack_channel_id:
        slack = SlackClient(secrets.slack_bot_token or "", dry_run=dry_run)
        stats["posted_to_slack"] = publish_pending(
            repo, slack=slack, channel_id=settings.policy.slack_channel_id
        )
    else:
        stats["posted_to_slack"] = 0  # no channel configured yet — see TEMPLATE_policy.yaml

    return stats


def run_poll_approvals(repo: Repo, *, dry_run: bool) -> dict:
    from bpb.approval.poller import confirm_sent_pending, decide_pending, release_stale_approvals
    from bpb.clients.hubspot import HubSpotClient
    from bpb.clients.slack import SlackClient
    from bpb.config import Secrets, load_settings

    settings = load_settings()
    secrets = Secrets()
    slack = SlackClient(secrets.slack_bot_token or "", dry_run=dry_run)
    hubspot = HubSpotClient(secrets.hubspot_private_app_token or "", dry_run=dry_run)
    approver_ids = set(settings.policy.approver_slack_user_ids)

    decide_stats = decide_pending(
        repo,
        slack=slack,
        hubspot=hubspot,
        approver_ids=approver_ids,
        ttl_hours=settings.policy.approval_ttl_hours,
        require_send_confirmation=settings.policy.require_send_confirmation,
    )
    confirm_stats = (
        confirm_sent_pending(repo, slack=slack, hubspot=hubspot, approver_ids=approver_ids)
        if settings.policy.require_send_confirmation
        else {"confirmed": 0, "still_unconfirmed": 0}
    )
    release_stats = release_stale_approvals(repo, ttl_days=settings.policy.send_confirm_ttl_days)

    return {"decide": decide_stats, "confirm": confirm_stats, "release": release_stats}


def run_report(repo: Repo, *, weekly: bool) -> str:
    from collections import Counter

    from bpb.ledger.credits import report as credits_report

    prospects = repo.list_prospects()
    queue_items = repo.list_queue_items()

    lines = [f"{'Weekly' if weekly else 'On-demand'} report", ""]

    lines.append("Prospects by status:")
    for status, count in sorted(Counter(p.status for p in prospects).items()):
        lines.append(f"  {status}: {count}")

    lines.append("")
    lines.append("Queue items by decision:")
    for decision, count in sorted(Counter(i.decision for i in queue_items).items()):
        lines.append(f"  {decision}: {count}")

    approved_unconfirmed = [
        i for i in queue_items if i.decision == "approved" and i.send_status == "unconfirmed"
    ]
    if approved_unconfirmed:
        lines.append("")
        lines.append(f"Approved but not yet confirmed sent: {len(approved_unconfirmed)}")

    lines.append("")
    lines.append("Credit spend:")
    lines.append(credits_report(repo))

    return "\n".join(lines)
