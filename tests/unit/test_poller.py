from datetime import UTC, datetime, timedelta

from bpb import models
from bpb.approval.poller import decide_pending
from bpb.clients.hubspot import HubSpotClient
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend

APPROVER = "U_APPROVER"
OTHER_USER = "U_RANDOM"


class FakeSlack:
    def __init__(self, reactions=None, replies=None):
        self._reactions = reactions or []
        self._replies = replies or []
        self.updated_messages = []

    def get_reactions(self, *, channel, ts):
        return self._reactions

    def get_replies(self, *, channel, ts):
        return self._replies

    def update_message(self, *, channel, ts, text):
        self.updated_messages.append((channel, ts, text))
        return {"ok": True}


def _repo():
    backend = MemoryBackend()
    return bootstrap(backend, run_id="test-run")


def _hubspot() -> HubSpotClient:
    return HubSpotClient("fake", dry_run=True)


def _decide(repo, slack, *, require_send_confirmation=True, **kwargs):
    return decide_pending(
        repo, slack=slack, hubspot=_hubspot(), require_send_confirmation=require_send_confirmation,
        **kwargs,
    )


def _posted_item(repo, **kwargs) -> models.QueueItem:
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    prospect = repo.upsert_prospect(models.Prospect(firm_id=firm.id, full_name="Jane Doe"))
    defaults = dict(
        prospect_id=prospect.id, channel="email", row_ref="Q-TEST-001", draft_body="Hi",
        slack_channel_id="C123", slack_message_ts="123.456", posted_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    item = repo.upsert_queue_item(models.QueueItem(**defaults))
    repo.flush()
    return item


def test_approve_reaction_from_approver_approves():
    repo = _repo()
    _posted_item(repo)
    slack = FakeSlack(reactions=[{"name": "white_check_mark", "users": [APPROVER]}])

    stats = _decide(repo, slack, approver_ids={APPROVER}, ttl_hours=168)

    assert stats["approved"] == 1
    item = repo.list_queue_items()[0]
    assert item.decision == "approved"
    assert item.decided_by == APPROVER
    assert item.decision_source == "reaction"


def test_approval_triggers_hand_off_suppression_and_outreach_log():
    repo = _repo()
    _posted_item(repo)
    slack = FakeSlack(reactions=[{"name": "white_check_mark", "users": [APPROVER]}])

    _decide(repo, slack, approver_ids={APPROVER}, ttl_hours=168)

    assert len(slack.updated_messages) == 1
    assert repo.list_suppressions()
    assert repo.list_outreach_log()
    item = repo.list_queue_items()[0]
    assert item.send_status == "unconfirmed"


def test_approval_without_send_confirmation_marks_sent_immediately():
    repo = _repo()
    _posted_item(repo)
    slack = FakeSlack(reactions=[{"name": "white_check_mark", "users": [APPROVER]}])

    _decide(repo, slack, approver_ids={APPROVER}, ttl_hours=168, require_send_confirmation=False)

    item = repo.list_queue_items()[0]
    assert item.send_status == "sent_manual"
    assert item.sent_at is not None


def test_approve_reaction_from_non_approver_is_ignored():
    repo = _repo()
    _posted_item(repo)
    slack = FakeSlack(reactions=[{"name": "white_check_mark", "users": [OTHER_USER]}])

    stats = _decide(repo, slack, approver_ids={APPROVER}, ttl_hours=168)

    assert stats["still_pending"] == 1
    assert repo.list_queue_items()[0].decision == "pending"


def test_reject_reaction_from_approver_rejects():
    repo = _repo()
    _posted_item(repo)
    slack = FakeSlack(reactions=[{"name": "x", "users": [APPROVER]}])

    stats = _decide(repo, slack, approver_ids={APPROVER}, ttl_hours=168)

    assert stats["rejected"] == 1
    assert repo.list_queue_items()[0].decision == "rejected"


def test_both_approve_and_reject_from_approvers_is_ambiguous():
    repo = _repo()
    _posted_item(repo)
    slack = FakeSlack(
        reactions=[
            {"name": "white_check_mark", "users": [APPROVER]},
            {"name": "x", "users": [APPROVER]},
        ]
    )

    stats = _decide(repo, slack, approver_ids={APPROVER}, ttl_hours=168)

    assert stats["ambiguous"] == 1
    assert repo.list_queue_items()[0].decision == "ambiguous"


def test_expired_after_ttl_with_no_reaction():
    repo = _repo()
    _posted_item(repo, posted_at=datetime.now(UTC) - timedelta(hours=200))
    slack = FakeSlack(reactions=[])

    stats = _decide(repo, slack, approver_ids={APPROVER}, ttl_hours=168)

    assert stats["expired"] == 1
    item = repo.list_queue_items()[0]
    assert item.decision == "expired"
    assert item.decision_source == "ttl_sweep"


def test_unposted_item_is_skipped():
    repo = _repo()
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    prospect = repo.upsert_prospect(models.Prospect(firm_id=firm.id, full_name="Jane Doe"))
    repo.upsert_queue_item(
        models.QueueItem(prospect_id=prospect.id, channel="email", row_ref="Q-1", draft_body="hi")
    )
    repo.flush()
    slack = FakeSlack()

    stats = _decide(repo, slack, approver_ids={APPROVER}, ttl_hours=168)

    assert stats["still_pending"] == 0  # never counted -- not posted yet
    assert repo.list_queue_items()[0].decision == "pending"


def test_edit_reaction_captures_thread_reply_starting_with_edit_prefix():
    repo = _repo()
    _posted_item(repo)
    slack = FakeSlack(
        reactions=[{"name": "pencil2", "users": [APPROVER]}],
        replies=[
            {"user": "bot", "text": "original message"},
            {"user": OTHER_USER, "text": "EDIT: not this one"},
            {"user": APPROVER, "text": "EDIT: use this wording instead"},
        ],
    )

    stats = _decide(repo, slack, approver_ids={APPROVER}, ttl_hours=168)

    assert stats["edited"] == 1
    assert repo.list_queue_items()[0].edited_body == "use this wording instead"


def test_edit_without_approve_or_reject_stays_pending():
    repo = _repo()
    _posted_item(repo)
    slack = FakeSlack(
        reactions=[{"name": "pencil2", "users": [APPROVER]}],
        replies=[
            {"user": "bot", "text": "original"},
            {"user": APPROVER, "text": "EDIT: better wording"},
        ],
    )

    _decide(repo, slack, approver_ids={APPROVER}, ttl_hours=168)

    item = repo.list_queue_items()[0]
    assert item.decision == "pending"
    assert item.edited_body == "better wording"


def test_edit_reply_from_non_approver_is_ignored():
    repo = _repo()
    _posted_item(repo)
    slack = FakeSlack(
        reactions=[{"name": "pencil2", "users": [APPROVER]}],
        replies=[
            {"user": "bot", "text": "original"},
            {"user": OTHER_USER, "text": "EDIT: sneaky edit"},
        ],
    )

    _decide(repo, slack, approver_ids={APPROVER}, ttl_hours=168)

    assert repo.list_queue_items()[0].edited_body is None
