from bpb.config import Secrets, load_settings


def test_load_settings_reads_all_yaml_without_secrets():
    settings = load_settings()
    assert settings.budget.apollo_lead_credit.monthly_cap == 175
    assert settings.llm.drafting.model == "claude-opus-5"
    assert settings.path_a.search_providers == ["rss"]
    assert settings.policy.b_only_policy in ("soft", "hold")


def test_secrets_require_lists_all_missing_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    secrets = Secrets(_env_file=None)
    raised = False
    try:
        secrets.require("anthropic_api_key", "apollo_api_key")
    except SystemExit as e:
        raised = True
        assert "ANTHROPIC_API_KEY" in str(e)
        assert "APOLLO_API_KEY" in str(e)
    assert raised, "expected SystemExit"


def test_secrets_require_passes_when_present():
    secrets = Secrets(_env_file=None, anthropic_api_key="sk-test")
    secrets.require("anthropic_api_key")  # must not raise
