"""Posts pending queue items to Slack — one message per item, plain text (Slack
`mrkdwn`, not full Block Kit, since a text summary is all this needs and it
keeps the dry-run fixture trivial). Idempotent: an item that already has a
`slack_message_ts` is never re-posted.
"""

from __future__ import annotations

from bpb import models
from bpb.clients.slack import SlackClient
from bpb.store.repo import Repo


def _get_signal(repo: Repo, signal_id: str | None) -> models.Signal | None:
    if signal_id is None:
        return None
    return next((s for s in repo.list_signals() if s.id == signal_id), None)


def build_message_text(
    item: models.QueueItem,
    prospect: models.Prospect,
    firm: models.Firm,
    signal: models.Signal | None,
) -> str:
    lines = [
        f"*{item.row_ref}* — {prospect.full_name} ({prospect.title or 'unknown title'}) "
        f"at {firm.name}",
        f"Channel: {item.channel}",
    ]
    if signal is not None:
        lines.append(
            f"Signal: {signal.publication or 'unknown publication'} — {signal.article_title or ''}"
        )
        lines.append(f"Hook: {signal.hook_summary}")
    if item.draft_subject:
        lines.append(f"Subject: {item.draft_subject}")
    lines.append("```")
    lines.append(item.draft_body)
    lines.append("```")
    lines.append(
        "React :white_check_mark: to approve · :x: to reject · "
        ":pencil2: then reply in-thread to edit"
    )
    return "\n".join(lines)


def publish_pending(repo: Repo, *, slack: SlackClient, channel_id: str) -> int:
    posted = 0
    for item in repo.list_queue_items(decision="pending"):
        if item.slack_message_ts:
            continue
        prospect = repo.get_prospect(item.prospect_id)
        if prospect is None:
            continue
        firm = repo.get_firm(prospect.firm_id)
        if firm is None:
            continue
        signal = _get_signal(repo, item.signal_id)

        text = build_message_text(item, prospect, firm, signal)
        response = slack.post_message(channel=channel_id, text=text)

        item.slack_channel_id = response["channel"]
        item.slack_message_ts = response["ts"]
        item.posted_at = models.utcnow()
        repo.upsert_queue_item(item)
        posted += 1

    repo.flush()
    return posted
