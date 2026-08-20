from bpb.gates.sanctions.matcher import load_index, screen_name
from bpb.gates.sanctions.refresh import refresh_all
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend


def test_refresh_all_dry_run_produces_snapshots_and_a_searchable_index(tmp_path, monkeypatch):
    from bpb.gates.sanctions import matcher as matcher_module

    index_path = tmp_path / "sanctions_index.pkl"
    monkeypatch.setattr(matcher_module, "INDEX_PATH", index_path)

    backend = MemoryBackend()
    repo = bootstrap(backend, run_id="test-run")

    snapshots = refresh_all(repo, dry_run=True)

    assert len(snapshots) == 5
    assert all(s.entry_count >= 1 for s in snapshots)
    assert all(s.drive_file_id is None for s in snapshots)  # dry-run never uploads

    index = load_index(index_path)
    assert len(index) >= 5

    result = screen_name("Fixture Test Entry", index)
    assert result.verdict == "match"
