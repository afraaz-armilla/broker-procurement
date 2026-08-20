"""The full two-stage reaction machine (§9): decide (approve/reject/ambiguous/
expired) -> on approval, hand off immediately (outreach/handoff.py: finalize
the Slack message, write the suppression, log to HubSpot) -> confirm-sent (📤)
-> TTL release for approved-but-never-confirmed items.

`reactions.get` is the primary signal — one call per pending/unconfirmed item,
well inside Slack's ~50/min Tier 3 limit for non-Marketplace apps.
`conversations.replies` (capped at 1 req/min for those apps) is only called for
items where an approver reacted ✏️, since edits are rare — never as the default
per-item check.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from bpb.clients.hubspot import HubSpotClient
from bpb.clients.slack import SlackClient
from bpb.outreach.handoff import confirm_sent, finalize_approval, release_unsent
from bpb.store.repo import Repo

logger = logging.getLogger(__name__)

APPROVE_EMOJI = "white_check_mark"
REJECT_EMOJI = "x"
EDIT_EMOJI = "pencil2"
CONFIRM_SENT_EMOJI = "outbox_tray"


def _reactor_user_ids(reactions: list[dict], emoji: str) -> set[str]:
    for r in reactions:
        if r.get("name") == emoji:
            return set(r.get("users", []))
    return set()


def _find_edit_reply(replies: list[dict], approver_ids: set[str]) -> str | None:
    for message in replies[1:]:  # index 0 is the parent message itself
        if message.get("user") not in approver_ids:
            continue
        text = message.get("text", "")
        if text.upper().startswith("EDIT:"):
            return text[len("EDIT:") :].strip()
    return None


def decide_pending(
    repo: Repo,
    *,
    slack: SlackClient,
    hubspot: HubSpotClient,
    approver_ids: set[str],
    ttl_hours: int,
    require_send_confirmation: bool,
) -> dict:
    stats = {
        "approved": 0, "rejected": 0, "ambiguous": 0, "expired": 0, "edited": 0, "still_pending": 0,
    }
    now = datetime.now(UTC)

    for item in repo.list_queue_items(decision="pending"):
        if not item.slack_message_ts or not item.slack_channel_id:
            continue  # not yet posted by publisher.py

        posted_at = item.posted_at or item.created_at
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=UTC)
        if now - posted_at > timedelta(hours=ttl_hours):
            item.decision = "expired"
            item.decided_at = now
            item.decision_source = "ttl_sweep"
            repo.upsert_queue_item(item)
            stats["expired"] += 1
            continue

        reactions = slack.get_reactions(channel=item.slack_channel_id, ts=item.slack_message_ts)
        approved_by = _reactor_user_ids(reactions, APPROVE_EMOJI) & approver_ids
        rejected_by = _reactor_user_ids(reactions, REJECT_EMOJI) & approver_ids
        wants_edit = bool(_reactor_user_ids(reactions, EDIT_EMOJI) & approver_ids)

        if wants_edit and not item.edited_body:
            replies = slack.get_replies(channel=item.slack_channel_id, ts=item.slack_message_ts)
            edit_text = _find_edit_reply(replies, approver_ids)
            if edit_text is not None:
                item.edited_body = edit_text
                repo.upsert_queue_item(item)
                stats["edited"] += 1

        if approved_by and rejected_by:
            item.decision = "ambiguous"
            repo.upsert_queue_item(item)
            stats["ambiguous"] += 1
        elif approved_by:
            item.decision = "approved"
            item.decided_at = now
            item.decided_by = next(iter(approved_by))
            item.decision_source = "reaction"
            finalize_approval(
                repo, item, slack=slack, hubspot=hubspot,
                require_send_confirmation=require_send_confirmation,
            )
            stats["approved"] += 1
        elif rejected_by:
            item.decision = "rejected"
            item.decided_at = now
            item.decided_by = next(iter(rejected_by))
            item.decision_source = "reaction"
            repo.upsert_queue_item(item)
            stats["rejected"] += 1
        else:
            stats["still_pending"] += 1

    repo.flush()
    return stats


def confirm_sent_pending(
    repo: Repo, *, slack: SlackClient, hubspot: HubSpotClient, approver_ids: set[str]
) -> dict:
    stats = {"confirmed": 0, "still_unconfirmed": 0}
    for item in repo.list_queue_items(decision="approved"):
        if item.send_status != "unconfirmed":
            continue
        if not item.slack_channel_id or not item.slack_message_ts:
            continue

        reactions = slack.get_reactions(channel=item.slack_channel_id, ts=item.slack_message_ts)
        confirmed_by = _reactor_user_ids(reactions, CONFIRM_SENT_EMOJI) & approver_ids
        if confirmed_by:
            confirm_sent(repo, item, hubspot=hubspot)
            stats["confirmed"] += 1
        else:
            stats["still_unconfirmed"] += 1

    repo.flush()
    return stats


def release_stale_approvals(repo: Repo, *, ttl_days: int) -> dict:
    now = datetime.now(UTC)
    stats = {"released": 0}
    for item in repo.list_queue_items(decision="approved"):
        if item.send_status != "unconfirmed":
            continue
        decided_at = item.decided_at or item.created_at
        if decided_at.tzinfo is None:
            decided_at = decided_at.replace(tzinfo=UTC)
        if now - decided_at > timedelta(days=ttl_days):
            release_unsent(repo, item)
            stats["released"] += 1

    repo.flush()
    return stats
