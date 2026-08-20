from bpb import models
from bpb.approval.publisher import publish_pending
from bpb.clients.slack import SlackClient
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend


def _repo():
    backend = MemoryBackend()
    return bootstrap(backend, run_id="test-run")


def _queue_item(repo, firm, prospect, **kwargs) -> models.QueueItem:
    defaults = dict(
        prospect_id=prospect.id, channel="email", row_ref="Q-TEST-001", draft_body="Hi there."
    )
    defaults.update(kwargs)
    return repo.upsert_queue_item(models.QueueItem(**defaults))


def test_publish_pending_posts_and_records_slack_metadata():
    repo = _repo()
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    prospect = repo.upsert_prospect(models.Prospect(firm_id=firm.id, full_name="Jane Doe"))
    _queue_item(repo, firm, prospect)
    repo.flush()

    slack = SlackClient("fake", dry_run=True)
    posted = publish_pending(repo, slack=slack, channel_id="C123")

    assert posted == 1
    item = repo.list_queue_items()[0]
    assert item.slack_channel_id == "C123"
    assert item.slack_message_ts is not None
    assert item.posted_at is not None


def test_publish_pending_is_idempotent_for_already_posted_items():
    repo = _repo()
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    prospect = repo.upsert_prospect(models.Prospect(firm_id=firm.id, full_name="Jane Doe"))
    _queue_item(repo, firm, prospect, slack_channel_id="C123", slack_message_ts="123.456")
    repo.flush()

    slack = SlackClient("fake", dry_run=True)
    posted = publish_pending(repo, slack=slack, channel_id="C123")

    assert posted == 0


def test_publish_pending_skips_non_pending_items():
    repo = _repo()
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    prospect = repo.upsert_prospect(models.Prospect(firm_id=firm.id, full_name="Jane Doe"))
    _queue_item(repo, firm, prospect, decision="approved")
    repo.flush()

    slack = SlackClient("fake", dry_run=True)
    posted = publish_pending(repo, slack=slack, channel_id="C123")

    assert posted == 0


def test_message_text_includes_signal_hook_when_present():
    from bpb.approval.publisher import build_message_text

    firm = models.Firm(name="Acme", domain="acme.example")
    prospect = models.Prospect(firm_id=firm.id, full_name="Jane Doe", title="VP")
    signal = models.Signal(
        firm_id=firm.id, url="https://x", url_hash="h", publication="Insurance Business",
        article_title="AI Insurance", hook_summary="wrote about AI coverage",
    )
    item = models.QueueItem(
        prospect_id=prospect.id, channel="email", row_ref="Q-TEST-001", draft_body="Body text"
    )
    text = build_message_text(item, prospect, firm, signal)
    assert "wrote about AI coverage" in text
    assert "Jane Doe" in text
    assert "Body text" in text
