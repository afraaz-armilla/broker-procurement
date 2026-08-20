"""Pure role banding (§6 of the build plan). No I/O — takes a prospect (plus an
optional linked Signal) and the roles config dict, returns a band + score.

Band 1 (public voice) is assigned purely from having a linked Path A signal — title
doesn't matter. Band 2 (decision-maker) requires BOTH seniority language AND a
line-of-business keyword in the title — a senior title with no LOB relevance (e.g.
"Head of Claims" at a brokerage) isn't what we're after. Band 3 (producer) only
needs a producer-shaped title: being a broker/producer at an already-target-listed
firm is itself the qualifying signal, no LOB keyword required (bonus points if one
is present).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import yaml

from bpb import models
from bpb.config import CONFIG_DIR

PUBLICATION_TIER_BONUS = 5.0  # flat bonus; all configured trade press treated equally for now
RECENCY_BONUS_MAX = 10.0
RECENCY_HALF_LIFE_DAYS = 30


def load_roles_config() -> dict:
    with (CONFIG_DIR / "roles.yaml").open() as f:
        return yaml.safe_load(f) or {}


@dataclass(frozen=True)
class RoleBandResult:
    band: Literal[1, 2, 3] | None
    score: float
    reason: str


def _recency_bonus(published_at: datetime | None, *, now: datetime) -> float:
    if published_at is None:
        return 0.0
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    age_days = max(0.0, (now - published_at).total_seconds() / 86400)
    # Simple exponential decay, capped — a same-week article is worth the full
    # bonus, a year-old one is worth almost none.
    decay = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
    return RECENCY_BONUS_MAX * decay


def band_prospect(
    prospect: models.Prospect,
    *,
    signal: models.Signal | None,
    roles_config: dict,
    now: datetime | None = None,
) -> RoleBandResult:
    now = now or datetime.now(UTC)

    if signal is not None:
        score = 100.0 + PUBLICATION_TIER_BONUS + _recency_bonus(signal.published_at, now=now)
        return RoleBandResult(1, score, "public_voice")

    title = (prospect.title or "").lower()
    seniority_hits = [s for s in roles_config.get("decision_maker_seniority", []) if s in title]
    lob_hits = [k for k in roles_config.get("line_of_business_keywords", []) if k in title]
    producer_hits = [p for p in roles_config.get("producer_titles", []) if p in title]

    if seniority_hits and lob_hits:
        score = 60.0 + 2 * len(seniority_hits) + 2 * len(lob_hits)
        return RoleBandResult(2, score, "decision_maker")

    if producer_hits:
        score = 30.0 + 2 * len(lob_hits)
        return RoleBandResult(3, score, "producer")

    return RoleBandResult(None, 0.0, "unbanded")
