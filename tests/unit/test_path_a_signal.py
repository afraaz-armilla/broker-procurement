from bpb.clients.anthropic_client import AnthropicClient
from bpb.config import load_settings
from bpb.discovery.path_a_signal import discover_signals
from bpb.store.bootstrap import bootstrap
from bpb.store.db import MemoryBackend


def _repo():
    backend = MemoryBackend()
    return bootstrap(backend, run_id="test-run")


def test_discover_signals_dry_run_creates_a_signal_and_prospect():
    repo = _repo()
    settings = load_settings()
    llm = AnthropicClient("fake", dry_run=True)

    stats = discover_signals(repo, settings=settings, llm=llm, dry_run=True)

    assert stats["signals_created"] >= 1
    signals = repo.list_signals()
    assert len(signals) == stats["signals_created"]

    prospects = [p for p in repo.list_prospects() if p.source_path == "A"]
    assert len(prospects) >= 1
    assert prospects[0].role_band is None  # banding happens in select_firm/sweep_firm, not here


def test_discover_signals_is_idempotent_on_the_same_url():
    repo = _repo()
    settings = load_settings()
    llm = AnthropicClient("fake", dry_run=True)

    discover_signals(repo, settings=settings, llm=llm, dry_run=True)
    first_count = len(repo.list_signals())
    discover_signals(repo, settings=settings, llm=llm, dry_run=True)
    second_count = len(repo.list_signals())

    assert first_count == second_count  # same fixture URL every time -> deduped


def test_off_target_firm_is_flagged_not_silently_dropped(monkeypatch):
    """The dry-run fixture's firm name ("Fixture Brokerage") won't fuzzy-match
    any real configured target firm, so it should land as on_target_list=False
    and be counted, not silently merged into an unrelated firm."""
    repo = _repo()
    settings = load_settings()
    llm = AnthropicClient("fake", dry_run=True)

    stats = discover_signals(repo, settings=settings, llm=llm, dry_run=True)

    assert stats["off_target_firms"] >= 1
    off_target = [f for f in repo.list_firms() if not f.on_target_list]
    assert len(off_target) >= 1


def test_signal_below_relevance_threshold_is_not_promoted():
    repo = _repo()
    settings = load_settings()
    settings.path_a.min_relevance = 0.95  # dry-run fixture scores 0.9 -> filtered out
    llm = AnthropicClient("fake", dry_run=True)

    stats = discover_signals(repo, settings=settings, llm=llm, dry_run=True)

    assert stats["signals_created"] == 0
    assert stats["below_relevance"] >= 1
