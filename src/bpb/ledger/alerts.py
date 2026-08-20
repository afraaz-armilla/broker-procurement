"""Budget alerting: surfaces warn/degrade/hard_stop crossings. Pluggable `notify`
callback so this works standalone (logs) today and gets wired to Slack in phase 8
without changing this module — `pipeline.py` passes a Slack-posting callback once
clients/slack.py exists.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from bpb.ledger.credits import BudgetStatus, CreditLedger
from bpb.models import CreditBucket

logger = logging.getLogger(__name__)

ALL_BUCKETS: tuple[CreditBucket, ...] = (
    "apollo_lead_credit",
    "hunter_search",
    "zerobounce_verification",
    "abstract_verification",
)


def default_notify(message: str) -> None:
    logger.warning(message)


def check_and_alert(
    ledger: CreditLedger,
    *,
    buckets: tuple[CreditBucket, ...] = ALL_BUCKETS,
    notify: Callable[[str], None] = default_notify,
) -> list[BudgetStatus]:
    """Call once per run, after discovery/enrichment stages have recorded their
    spend. Returns every bucket's status (for the weekly report) and notifies for
    anything at warn tier or worse."""
    statuses = [ledger.status(b) for b in buckets]
    for status in statuses:
        if status.tier == "ok":
            continue
        notify(_format_alert(status))
    return statuses


def _format_alert(status: BudgetStatus) -> str:
    live = (
        f", provider reports {status.live_remaining} remaining"
        if status.live_remaining is not None
        else ""
    )
    return (
        f"[{status.tier.upper()}] {status.bucket}: {status.local_spent}/{status.cap} credits "
        f"used this cycle ({status.pct_used:.0f}%){live}."
    )
