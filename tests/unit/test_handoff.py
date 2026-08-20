
from bpb import models
from bpb.clients.hubspot import HubSpotClient
from bpb.clients.slack import SlackClient
from bpb.outreach.handoff import confirm_sent, finalize_approval, release_unsent
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend


def _repo():
    backend = MemoryBackend()
    return bootstrap(backend, run_id="test-run")


def _setup(repo, **item_kwargs):
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    prospect = repo.upsert_prospect(
        models.Prospect(firm_id=firm.id, full_name="Jane Doe", first_name="Jane", last_name="Doe")
    )
    email = repo.add_email(
        models.Email(prospect_id=prospect.id, address="jane@acme.example", source="inferred")
    )
    defaults = dict(
        prospect_id=prospect.id, channel="email", row_ref="Q-TEST-001", draft_subject="Hi",
        draft_body="Draft body", decision="approved",
        slack_channel_id="C123", slack_message_ts="123.456",
    )
    defaults.update(item_kwargs)
    item = repo.upsert_queue_item(models.QueueItem(**defaults))
    repo.flush()
    return firm, prospect, email, item


def test_finalize_approval_writes_suppression_and_outreach_log():
    repo = _repo()
    _, prospect, email, item = _setup(repo)
    slack = SlackClient("fake", dry_run=True)
    hubspot = HubSpotClient("fake", dry_run=True)

    entry = finalize_approval(
        repo, item, slack=slack, hubspot=hubspot, require_send_confirmation=True
    )

    assert entry.status == "handed_to_phil"
    assert repo.is_suppressed("email", email.address)
    from bpb.gates.suppression import normalize_person_key

    assert repo.is_suppressed("person", normalize_person_key(prospect.firm_id, prospect.full_name))
    reloaded = repo.list_queue_items()[0]
    assert reloaded.send_status == "unconfirmed"


def test_finalize_approval_is_idempotent():
    repo = _repo()
    _, _, _, item = _setup(repo)
    slack = SlackClient("fake", dry_run=True)
    hubspot = HubSpotClient("fake", dry_run=True)

    first = finalize_approval(
        repo, item, slack=slack, hubspot=hubspot, require_send_confirmation=True
    )
    second = finalize_approval(
        repo, item, slack=slack, hubspot=hubspot, require_send_confirmation=True
    )

    assert first.id == second.id
    assert len(repo.list_outreach_log()) == 1


def test_finalize_approval_uses_edited_body_when_present():
    repo = _repo()
    _, _, _, item = _setup(repo, edited_body="Edited final wording")
    slack = SlackClient("fake", dry_run=True)
    hubspot = HubSpotClient("fake", dry_run=True)

    entry = finalize_approval(
        repo, item, slack=slack, hubspot=hubspot, require_send_confirmation=True
    )

    assert entry.body_final == "Edited final wording"


def test_finalize_approval_without_send_confirmation_marks_sent():
    repo = _repo()
    _, _, _, item = _setup(repo)
    slack = SlackClient("fake", dry_run=True)
    hubspot = HubSpotClient("fake", dry_run=True)

    finalize_approval(repo, item, slack=slack, hubspot=hubspot, require_send_confirmation=False)

    reloaded = repo.list_queue_items()[0]
    assert reloaded.send_status == "sent_manual"
    assert reloaded.sent_at is not None


def test_confirm_sent_marks_item_and_logs_second_outreach_entry():
    repo = _repo()
    _, _, _, item = _setup(repo, send_status="unconfirmed")
    hubspot = HubSpotClient("fake", dry_run=True)
    repo.add_outreach_log(
        models.OutreachLogEntry(
            prospect_id=item.prospect_id, queue_item_id=item.id, channel="email",
            status="handed_to_phil", idempotency_key=item.id,
            hubspot_contact_id="fixture-contact-id", body_final="Draft body",
        )
    )
    repo.flush()

    confirm_sent(repo, item, hubspot=hubspot)

    reloaded = repo.list_queue_items()[0]
    assert reloaded.send_status == "sent_manual"
    assert reloaded.sent_at is not None
    log_entries = repo.list_outreach_log(item.prospect_id)
    assert any(e.status == "sent_manual" for e in log_entries)


def test_release_unsent_clears_suppression_and_returns_to_pool():
    repo = _repo()
    _, prospect, email, item = _setup(repo, send_status="unconfirmed")
    from bpb.gates import suppression

    suppression.suppress_person(repo, prospect, reason="already_contacted", source="approval")
    repo.flush()

    release_unsent(repo, item)

    reloaded_item = repo.list_queue_items()[0]
    assert reloaded_item.send_status == "released"
    reloaded_prospect = repo.get_prospect(prospect.id)
    assert reloaded_prospect.status == "selected"
    assert suppression.is_prospect_suppressed(repo, prospect) is False
    assert any(e.status == "released" for e in repo.list_outreach_log(prospect.id))
