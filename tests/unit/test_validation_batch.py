from bpb.reporting.validation_batch import run_validation_batch
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend


def _repo():
    backend = MemoryBackend()
    return bootstrap(backend, run_id="test-run")


def test_validation_batch_dry_run_produces_a_report(tmp_path, monkeypatch):
    from bpb.gates.sanctions import matcher as matcher_module

    monkeypatch.setattr(matcher_module, "INDEX_PATH", tmp_path / "sanctions_index.pkl")

    repo = _repo()
    report = run_validation_batch(repo, city="New York", limit=20, path="b", dry_run=True)

    assert "Validation batch" in report
    assert "Email-reachable rate:" in report
    assert "LinkedIn-reachable rate:" in report
    # TEMPLATE_target_firms.yaml's one example firm, dry-run Apollo fixture yields
    # exactly one selected prospect who resolves and verifies as valid.
    assert "Email-reachable rate:    1/1 (100%)" in report


def test_validation_batch_path_a_only_runs_discovery_and_reports_it(tmp_path, monkeypatch):
    from bpb.gates.sanctions import matcher as matcher_module

    monkeypatch.setattr(matcher_module, "INDEX_PATH", tmp_path / "sanctions_index.pkl")
    repo = _repo()

    report = run_validation_batch(repo, city="New York", limit=20, path="a", dry_run=True)

    assert "Path A discovery:" in report
    assert "'signals_created': 1" in report


def test_validation_batch_both_paths_runs_without_error(tmp_path, monkeypatch):
    from bpb.gates.sanctions import matcher as matcher_module

    monkeypatch.setattr(matcher_module, "INDEX_PATH", tmp_path / "sanctions_index.pkl")
    repo = _repo()

    report = run_validation_batch(repo, city="New York", limit=20, path="both", dry_run=True)

    assert "Path A discovery:" in report
    assert "Email-reachable rate:" in report


def test_validation_batch_respects_limit_and_reports_dropped(tmp_path, monkeypatch):
    from bpb.gates.sanctions import matcher as matcher_module

    monkeypatch.setattr(matcher_module, "INDEX_PATH", tmp_path / "sanctions_index.pkl")
    repo = _repo()

    report = run_validation_batch(repo, city="New York", limit=0, path="b", dry_run=True)

    assert "dropped 1 over the limit" in report
