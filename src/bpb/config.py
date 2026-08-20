"""Config loading: YAML settings (non-secret) + env-var secrets, with fail-fast
validation of whatever a given command actually needs — dry-run and the unit
suite need none of this, so secrets are optional at the type level and checked
explicitly via `Secrets.require(...)` by commands that go live.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str | None = None
    apollo_api_key: str | None = None
    hunter_api_key: str | None = None
    zerobounce_api_key: str | None = None
    abstract_api_key: str | None = None
    hubspot_private_app_token: str | None = None
    slack_bot_token: str | None = None
    google_sheets_spreadsheet_id: str | None = None
    google_drive_folder_id: str | None = None
    gcp_service_account_key_b64: str | None = None
    brave_search_api_key: str | None = None

    def require(self, *names: str) -> None:
        """Raise once, listing every missing key, rather than failing on the first."""
        missing = [n for n in names if not getattr(self, n, None)]
        if missing:
            env_names = [n.upper() for n in missing]
            raise SystemExit(
                f"Missing required secret(s): {', '.join(env_names)}. "
                f"Set them in .env (local) or as GitHub Actions secrets (CI)."
            )


class BudgetBucket(BaseModel):
    monthly_cap: int
    warn_at_pct: int = 70
    degrade_at_pct: int = 85
    hard_stop_at_pct: int = 95


class BudgetSettings(BaseModel):
    apollo_lead_credit: BudgetBucket
    hunter_search: BudgetBucket
    zerobounce_verification: BudgetBucket
    abstract_verification: BudgetBucket

    def bucket(self, name: str) -> BudgetBucket:
        return getattr(self, name)


class LlmTaskConfig(BaseModel):
    model: str
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_tokens: int = 2000


class LlmSettings(BaseModel):
    extraction: LlmTaskConfig
    drafting: LlmTaskConfig


class PathASettings(BaseModel):
    search_providers: list[Literal["rss", "brave", "anthropic_hosted", "google_cse"]] = ["rss"]
    min_relevance: float = 0.6
    min_article_chars: int = 600


class RoleSettings(BaseModel):
    max_active_per_firm: int = 3
    stale_after_days: int = 45


class SanctionsSettings(BaseModel):
    potential_match_threshold: int = 92
    refresh_cadence_hours: int = 24


class PolicySettings(BaseModel):
    b_only_policy: Literal["soft", "hold"] = "hold"
    approver_slack_user_ids: list[str] = Field(default_factory=list)
    slack_channel_id: str = ""
    require_send_confirmation: bool = True
    approval_ttl_hours: int = 168
    send_confirm_ttl_days: int = 14
    allow_catch_all_emails: bool = False
    max_pattern_guesses: int = 1


class Settings(BaseModel):
    budget: BudgetSettings
    llm: LlmSettings
    path_a: PathASettings
    roles: RoleSettings
    sanctions: SanctionsSettings
    policy: PolicySettings
    raw_sources: dict = Field(default_factory=dict)
    raw_roles: dict = Field(default_factory=dict)
    raw_target_firms: dict = Field(default_factory=dict)
    raw_cities: dict = Field(default_factory=dict)
    raw_message_sequence: dict = Field(default_factory=dict)


def _load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Settings:
    settings_yaml = _load_yaml("settings.yaml")
    llm_yaml = _load_yaml("llm.yaml")
    budget_yaml = _load_yaml("budget.yaml")
    roles_yaml = _load_yaml("roles.yaml")
    sanctions_yaml = _load_yaml("sanctions.yaml")
    policy_yaml = _load_yaml("TEMPLATE_policy.yaml")

    return Settings(
        budget=BudgetSettings(**budget_yaml),
        llm=LlmSettings(**llm_yaml),
        path_a=PathASettings(**settings_yaml.get("path_a", {})),
        roles=RoleSettings(**settings_yaml.get("roles", {})),
        sanctions=SanctionsSettings(**sanctions_yaml.get("matching", {})),
        policy=PolicySettings(**policy_yaml.get("policy", {})),
        raw_sources=_load_yaml("sources.yaml"),
        raw_roles=roles_yaml,
        raw_target_firms=_load_yaml("TEMPLATE_target_firms.yaml"),
        raw_cities=_load_yaml("TEMPLATE_cities.yaml"),
        raw_message_sequence=_load_yaml("TEMPLATE_message_sequence.yaml"),
    )
