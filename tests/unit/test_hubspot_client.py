from bpb.clients.hubspot import HubSpotClient


def test_dry_run_upsert_contact_by_email_returns_fixture_id():
    client = HubSpotClient("fake", dry_run=True)
    contact_id = client.upsert_contact(
        email="jane@example.com", properties={"firstname": "Jane", "lastname": "Doe"}
    )
    assert contact_id


def test_dry_run_upsert_contact_without_email_falls_back_to_name_search():
    client = HubSpotClient("fake", dry_run=True)
    contact_id = client.upsert_contact(
        email=None, properties={"firstname": "Jane", "lastname": "Doe", "company": "Acme"}
    )
    assert contact_id


def test_dry_run_create_note_returns_fixture_id():
    client = HubSpotClient("fake", dry_run=True)
    note_id = client.create_note(body="hello", contact_id="c1", timestamp_ms=1700000000000)
    assert note_id == "fixture-note-id"
