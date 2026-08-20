from bpb import models
from bpb.pipeline import run_report
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend


def test_run_report_summarizes_prospects_and_queue_items():
    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="test-run")
    firm = repo.upsert_firm(models.Firm(name="Acme", domain="acme.example"))
    repo.upsert_prospect(models.Prospect(firm_id=firm.id, full_name="Jane Doe", status="selected"))
    repo.upsert_prospect(models.Prospect(firm_id=firm.id, full_name="John Roe", status="drafted"))
    repo.upsert_queue_item(
        models.QueueItem(
            prospect_id="p1", channel="email", row_ref="Q-1", draft_body="hi",
            decision="approved", send_status="unconfirmed",
        )
    )
    repo.flush()

    report = run_report(repo, weekly=True)

    assert "Weekly report" in report
    assert "selected: 1" in report
    assert "drafted: 1" in report
    assert "approved: 1" in report
    assert "Approved but not yet confirmed sent: 1" in report
