from bpb import models
from bpb.pipeline import run_assemble, run_discover
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend


def _repo():
    backend = MemoryBackend()
    return bootstrap(backend, run_id="test-run")


def test_assemble_end_to_end_dry_run_creates_an_email_queue_item(tmp_path, monkeypatch):
    from bpb import config as config_module
    from bpb.gates.sanctions import matcher as matcher_module

    monkeypatch.setattr(matcher_module, "INDEX_PATH", tmp_path / "sanctions_index.pkl")
    # TEMPLATE_policy.yaml defaults b_only_policy to "hold" — override to "soft" so
    # this Path-B prospect (no linked signal) actually gets drafted, to exercise
    # the full gate cascade end to end.
    real_load_settings = config_module.load_settings

    def soft_policy_settings():
        settings = real_load_settings()
        settings.policy.b_only_policy = "soft"
        return settings

    monkeypatch.setattr(config_module, "load_settings", soft_policy_settings)

    repo = _repo()
    run_discover(repo, path="b", firm_domain=None, city=None, dry_run=True)
    stats = run_assemble(repo, dry_run=True)

    assert stats["queue_items_created"] >= 1
    items = repo.list_queue_items()
    email_items = [i for i in items if i.channel == "email"]
    assert len(email_items) == 1
    assert email_items[0].decision == "pending"
    assert email_items[0].draft_body

    prospects = repo.list_prospects()
    assert any(p.status == "drafted" for p in prospects)


def test_assemble_skips_prospects_that_are_not_selected(tmp_path, monkeypatch):
    from bpb.gates.sanctions import matcher as matcher_module

    monkeypatch.setattr(matcher_module, "INDEX_PATH", tmp_path / "sanctions_index.pkl")

    repo = _repo()
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    repo.upsert_prospect(
        models.Prospect(firm_id=firm.id, full_name="Discovered Only", status="discovered")
    )
    repo.flush()

    stats = run_assemble(repo, dry_run=True)

    assert stats["queue_items_created"] == 0
    assert repo.list_queue_items() == []


def test_assemble_disqualifies_a_sanctioned_prospect(tmp_path, monkeypatch):
    from bpb.gates.sanctions import matcher as matcher_module
    from bpb.gates.sanctions import refresh as refresh_module
    from bpb.gates.sanctions.matcher import SanctionsEntry, build_index, save_index

    index_path = tmp_path / "sanctions_index.pkl"
    monkeypatch.setattr(matcher_module, "INDEX_PATH", index_path)
    index = build_index([SanctionsEntry(list_source="ofac_sdn", name="Fixture Broker")])
    save_index(index, index_path)
    # run_assemble refreshes the sanctions index itself (free, real behavior) —
    # stub that out here so it doesn't overwrite the index this test just built.
    monkeypatch.setattr(refresh_module, "refresh_all", lambda repo, dry_run=False: [])

    repo = _repo()
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    repo.upsert_prospect(
        models.Prospect(
            firm_id=firm.id, full_name="Fixture Broker", role_band=3, role_score=30,
            status="selected", source_path="B",
        )
    )
    repo.flush()

    stats = run_assemble(repo, dry_run=True)

    assert stats["disqualified"].get("sanctions_match") == 1
    prospects = repo.list_prospects()
    assert prospects[0].status == "disqualified_sanctions"
    suppressions = repo.list_suppressions()
    assert any(s.scope == "person" for s in suppressions)


def test_assemble_suppressed_prospect_is_skipped(tmp_path, monkeypatch):
    from bpb.gates import suppression
    from bpb.gates.sanctions import matcher as matcher_module

    monkeypatch.setattr(matcher_module, "INDEX_PATH", tmp_path / "sanctions_index.pkl")

    repo = _repo()
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    prospect = repo.upsert_prospect(
        models.Prospect(
            firm_id=firm.id, full_name="Jane Doe", role_band=3, role_score=30,
            status="selected", source_path="B",
        )
    )
    suppression.suppress_person(repo, prospect, reason="already_contacted", source="test")
    repo.flush()

    stats = run_assemble(repo, dry_run=True)

    assert stats["disqualified"].get("suppressed") == 1
    assert repo.list_prospects()[0].status == "suppressed"
    assert repo.list_queue_items() == []


def test_assemble_top_tier_path_a_prospect_gets_both_channels(tmp_path, monkeypatch):
    from bpb.gates.sanctions import matcher as matcher_module

    monkeypatch.setattr(matcher_module, "INDEX_PATH", tmp_path / "sanctions_index.pkl")

    repo = _repo()
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    prospect = repo.upsert_prospect(
        models.Prospect(
            firm_id=firm.id, full_name="Jane Doe", first_name="Jane", last_name="Doe",
            role_band=1, role_score=105, status="selected", source_path="A",
            linkedin_url="https://linkedin.com/in/jane",
        )
    )
    repo.add_signal(
        models.Signal(
            firm_id=firm.id, prospect_id=prospect.id, url="https://example.com/a", url_hash="h1",
            publication="Insurance Business", article_title="AI Insurance", hook_summary="hook",
            evidence_quote="evidence",
        )
    )
    repo.flush()

    stats = run_assemble(repo, dry_run=True)

    items = repo.list_queue_items()
    channels = {i.channel for i in items}
    assert channels == {"email", "linkedin"}
    assert stats["queue_items_created"] == 2


def test_assemble_b_only_hold_policy_skips_without_drafting(tmp_path, monkeypatch):
    from bpb.gates.sanctions import matcher as matcher_module

    monkeypatch.setattr(matcher_module, "INDEX_PATH", tmp_path / "sanctions_index.pkl")

    repo = _repo()
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    repo.upsert_prospect(
        models.Prospect(
            firm_id=firm.id, full_name="Jane Doe", first_name="Jane", last_name="Doe",
            role_band=3, role_score=30, status="selected", source_path="B",
        )
    )
    repo.flush()

    # TEMPLATE_policy.yaml defaults b_only_policy to "hold" and path_b_hold has send: false
    stats = run_assemble(repo, dry_run=True)

    assert stats["disqualified"].get("b_only_hold") == 1
    assert repo.list_queue_items() == []
    # status stays "selected" (not needs_manual) -- held, not broken
    assert repo.list_prospects()[0].status == "selected"
