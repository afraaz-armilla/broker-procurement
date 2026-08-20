"""Domain objects persisted to the Google Sheets store.

Every model corresponds to one tab (see store/sheets_schema.py, which derives its
canonical column order from these field lists). Field order here IS column order.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field
from ulid import ULID


def new_id() -> str:
    return str(ULID())


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class BpbModel(BaseModel):
    """Base for every persisted row: id + audit columns + a human-editable notes field."""

    id: str = Field(default_factory=new_id)
    created_at: dt.datetime = Field(default_factory=utcnow)


class MutableModel(BpbModel):
    """Base for rows in mutable tabs — carries the optimistic-concurrency version."""

    updated_at: dt.datetime = Field(default_factory=utcnow)
    row_version: int = 1
    notes: str = ""  # bot never reads or writes this field


CoverageState = Literal["untouched", "path_a_hit", "path_b_swept", "exhausted"]


class Firm(MutableModel):
    name: str
    domain: str | None = None
    city: str | None = None
    tier: str | None = None
    coverage_state: CoverageState = "untouched"
    email_pattern: str | None = None
    pattern_source: Literal["hunter", "learned", "config_override", "global_default"] | None = None
    pattern_confidence: float = 0.0
    is_catch_all: bool | None = None
    last_swept_at: dt.datetime | None = None
    on_target_list: bool = True


RoleBand = Literal[1, 2, 3]
SourcePath = Literal["A", "B"]
ProspectStatus = Literal[
    "discovered",
    "selected",
    "reserved",
    "screened",
    "email_resolved",
    "verified",
    "drafted",
    "queued",
    "approved",
    "rejected",
    "sent",
    "disqualified_sanctions",
    "disqualified_email",
    "suppressed",
    "needs_manual",
]


class Prospect(MutableModel):
    firm_id: str
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    role_band: RoleBand | None = None
    role_score: float = 0.0
    source_path: SourcePath = "B"
    linkedin_url: str | None = None
    city: str | None = None
    status: ProspectStatus = "discovered"
    reserve_rank: int | None = None
    last_activity_at: dt.datetime | None = None


class Signal(BpbModel):
    firm_id: str
    prospect_id: str | None = None
    url: str
    url_hash: str
    publication: str | None = None
    article_title: str | None = None
    published_at: dt.datetime | None = None
    hook_summary: str = ""
    evidence_quote: str = ""
    relevance: float = 0.0
    raw_json: str = "{}"


EmailSource = Literal["apollo", "hunter_domain", "hunter_finder", "inferred"]


class Email(BpbModel):
    prospect_id: str
    address: str
    source: EmailSource
    pattern_used: str | None = None
    is_current: bool = True


class Verification(BpbModel):
    email_id: str
    provider: Literal["zerobounce", "abstract"]
    status: str
    sub_status: str | None = None
    score: float | None = None
    credits_charged: int = 0
    raw_json: str = "{}"
    checked_at: dt.datetime = Field(default_factory=utcnow)


ScreeningVerdict = Literal["clear", "potential_match", "match"]


class Screening(BpbModel):
    prospect_id: str
    verdict: ScreeningVerdict
    best_score: float = 0.0
    matched_entry_name: str | None = None
    matched_lists_json: str = "[]"
    snapshot_ids_json: str = "[]"
    matcher_version: str = "1"
    screened_at: dt.datetime = Field(default_factory=utcnow)


SanctionsListSource = Literal[
    "ofac_sdn", "ofac_consolidated", "uksl", "canada_sema", "un_consolidated"
]


class SanctionsSnapshot(BpbModel):
    list_source: SanctionsListSource
    url: str
    fetched_at: dt.datetime = Field(default_factory=utcnow)
    sha256: str
    entry_count: int = 0
    parser_version: str = "1"
    drive_file_id: str | None = None


CreditBucket = Literal[
    "apollo_lead_credit", "hunter_search", "zerobounce_verification", "abstract_verification"
]
CreditProvider = Literal["apollo", "hunter", "zerobounce", "abstract"]


class CreditLedgerEntry(BpbModel):
    provider: CreditProvider
    bucket: CreditBucket
    endpoint: str
    credits_charged: int
    prospect_id: str | None = None
    firm_id: str | None = None
    run_id: str
    provider_reported_remaining: int | None = None
    occurred_at: dt.datetime = Field(default_factory=utcnow)
    note: str = ""


QueueChannel = Literal["email", "linkedin"]
QueueDecision = Literal["pending", "approved", "rejected", "expired", "ambiguous", "needs_review"]
SendStatus = Literal["unconfirmed", "sent_manual", "released"]


class QueueItem(MutableModel):
    prospect_id: str
    channel: QueueChannel
    row_ref: str
    draft_subject: str | None = None
    draft_body: str
    edited_body: str | None = None
    signal_id: str | None = None
    template_version: str = "1"
    slack_channel_id: str | None = None
    slack_message_ts: str | None = None
    posted_at: dt.datetime | None = None
    decision: QueueDecision = "pending"
    decided_at: dt.datetime | None = None
    decided_by: str | None = None
    decision_source: Literal["reaction", "ttl_sweep"] | None = None
    send_status: SendStatus = "unconfirmed"
    sent_at: dt.datetime | None = None


OutreachStatus = Literal["handed_to_phil", "sent_manual", "released"]


class OutreachLogEntry(BpbModel):
    prospect_id: str
    queue_item_id: str
    channel: QueueChannel
    status: OutreachStatus
    idempotency_key: str
    hubspot_contact_id: str | None = None
    hubspot_note_id: str | None = None
    subject: str | None = None
    body_final: str
    handed_at: dt.datetime = Field(default_factory=utcnow)
    sent_at: dt.datetime | None = None


SuppressionScope = Literal["email", "person", "firm", "domain"]
SuppressionReason = Literal[
    "already_contacted", "opt_out", "bounced", "sanctions_hit", "manual", "reply_received"
]


class Suppression(MutableModel):
    scope: SuppressionScope
    key_normalized: str
    reason: SuppressionReason
    source: str
    expires_at: dt.datetime | None = None
    released_at: dt.datetime | None = None


RunMode = Literal["live", "dry_run", "validation"]


class Run(BpbModel):
    run_id: str
    command: str
    mode: RunMode
    git_sha: str | None = None
    event: Literal["started", "finished", "failed"] = "started"
    status: str | None = None
    stats_json: str = "{}"
    occurred_at: dt.datetime = Field(default_factory=utcnow)


class Lock(MutableModel):
    name: str
    holder_run_id: str
    acquired_at: dt.datetime = Field(default_factory=utcnow)
    expires_at: dt.datetime
