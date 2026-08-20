"""Replaces "sending" (§10) — there is none. On approval: finalize the Slack
message in place, write the suppression NOW (a contacted broker can never
double-surface, whether or not Phil ever confirms he actually sent it), and log
to HubSpot. Idempotent via `OutreachLogEntry.idempotency_key = queue_item_id`,
so a crash-and-retry never double-logs to HubSpot.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from bpb import models
from bpb.clients.hubspot import HubSpotClient
from bpb.clients.slack import SlackClient
from bpb.gates import suppression as suppression_gate
from bpb.store.repo import Repo

logger = logging.getLogger(__name__)


def _prospect_email(repo: Repo, prospect_id: str) -> models.Email | None:
    current = [e for e in repo.list_emails(prospect_id) if e.is_current]
    return current[-1] if current else None


def _finalize_message_text(item: models.QueueItem, final_body: str) -> str:
    lines = [f"*{item.row_ref}* — APPROVED, ready to send"]
    if item.channel == "email" and item.draft_subject:
        lines.append(f"Subject: {item.draft_subject}")
    lines.append("```")
    lines.append(final_body)
    lines.append("```")
    lines.append("React :outbox_tray: once you've actually sent it.")
    return "\n".join(lines)


def finalize_approval(
    repo: Repo,
    item: models.QueueItem,
    *,
    slack: SlackClient,
    hubspot: HubSpotClient,
    require_send_confirmation: bool,
) -> models.OutreachLogEntry:
    prospect = repo.get_prospect(item.prospect_id)
    assert prospect is not None
    firm = repo.get_firm(prospect.firm_id)
    assert firm is not None

    final_body = item.edited_body or item.draft_body

    if item.slack_channel_id and item.slack_message_ts:
        slack.update_message(
            channel=item.slack_channel_id,
            ts=item.slack_message_ts,
            text=_finalize_message_text(item, final_body),
        )

    if item.channel == "email":
        email = _prospect_email(repo, prospect.id)
        if email is not None:
            suppression_gate.suppress_email(
                repo, email.address, reason="already_contacted", source="approval"
            )
    suppression_gate.suppress_person(repo, prospect, reason="already_contacted", source="approval")

    existing = next(
        (
            o
            for o in repo.list_outreach_log(prospect.id)
            if o.idempotency_key == item.id
        ),
        None,
    )
    if existing is not None:
        return existing

    contact_id, note_id = _log_approval_to_hubspot(repo, hubspot, prospect, firm, item, final_body)

    entry = repo.add_outreach_log(
        models.OutreachLogEntry(
            prospect_id=prospect.id,
            queue_item_id=item.id,
            channel=item.channel,
            status="handed_to_phil",
            idempotency_key=item.id,
            hubspot_contact_id=contact_id,
            hubspot_note_id=note_id,
            subject=item.draft_subject,
            body_final=final_body,
        )
    )

    item.send_status = "unconfirmed" if require_send_confirmation else "sent_manual"
    if not require_send_confirmation:
        item.sent_at = datetime.now(UTC)
    repo.upsert_queue_item(item)

    return entry


def _log_approval_to_hubspot(
    repo: Repo,
    hubspot: HubSpotClient,
    prospect: models.Prospect,
    firm: models.Firm,
    item: models.QueueItem,
    final_body: str,
) -> tuple[str | None, str | None]:
    email = _prospect_email(repo, prospect.id)
    properties = {
        "firstname": prospect.first_name or prospect.full_name.split()[0],
        "lastname": prospect.last_name or "",
        "company": firm.name,
        "armilla_outreach_state": "ready_to_send",
    }
    try:
        contact_id = hubspot.upsert_contact(
            email=email.address if email else None, properties=properties
        )
    except Exception:
        logger.warning("HubSpot contact upsert failed for prospect %s", prospect.id, exc_info=True)
        return None, None

    note_body = (
        f"Outreach approved and handed to Phil for manual send.\nChannel: {item.channel}\n\n"
        f"{final_body}"
    )
    try:
        note_id: str | None = hubspot.create_note(
            body=note_body, contact_id=contact_id, timestamp_ms=_now_ms()
        )
    except Exception:
        logger.warning("HubSpot note create failed for prospect %s", prospect.id, exc_info=True)
        note_id = None

    return contact_id, note_id


def confirm_sent(repo: Repo, item: models.QueueItem, *, hubspot: HubSpotClient) -> None:
    """Called on the 📤 confirmation reaction."""
    item.send_status = "sent_manual"
    item.sent_at = datetime.now(UTC)
    repo.upsert_queue_item(item)

    matching = [o for o in repo.list_outreach_log(item.prospect_id) if o.queue_item_id == item.id]
    prior = matching[-1] if matching else None

    if prior is not None and prior.hubspot_contact_id:
        try:
            hubspot.create_note(
                body=f"Sent manually by Phil on {item.sent_at.date().isoformat()}.\n"
                f"Channel: {item.channel}",
                contact_id=prior.hubspot_contact_id,
                timestamp_ms=_now_ms(),
            )
        except Exception:
            logger.warning(
                "HubSpot sent-confirmation note failed for item %s", item.id, exc_info=True
            )

    repo.add_outreach_log(
        models.OutreachLogEntry(
            prospect_id=item.prospect_id,
            queue_item_id=item.id,
            channel=item.channel,
            status="sent_manual",
            idempotency_key=f"{item.id}:sent",
            hubspot_contact_id=prior.hubspot_contact_id if prior else None,
            subject=item.draft_subject,
            body_final=prior.body_final if prior else (item.edited_body or item.draft_body),
            sent_at=item.sent_at,
        )
    )


def release_unsent(repo: Repo, item: models.QueueItem) -> None:
    """TTL sweep: approved-but-unconfirmed past send_confirm_ttl_days — clears the
    suppression and returns the prospect to the pool rather than burning the lead
    on a Phil-forgot-to-send silently."""
    item.send_status = "released"
    repo.upsert_queue_item(item)

    prospect = repo.get_prospect(item.prospect_id)
    if prospect is not None:
        key = suppression_gate.normalize_person_key(prospect.firm_id, prospect.full_name)
        for s in repo.list_suppressions():
            if s.scope == "person" and s.key_normalized == key and s.released_at is None:
                s.released_at = datetime.now(UTC)
                repo.upsert_suppression(s)
        prospect.status = "selected"
        repo.upsert_prospect(prospect)

    repo.add_outreach_log(
        models.OutreachLogEntry(
            prospect_id=item.prospect_id,
            queue_item_id=item.id,
            channel=item.channel,
            status="released",
            idempotency_key=f"{item.id}:released",
            subject=item.draft_subject,
            body_final=item.edited_body or item.draft_body,
        )
    )


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)
