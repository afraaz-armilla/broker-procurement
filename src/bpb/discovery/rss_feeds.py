"""Default Path A provider: RSS/Atom feed monitoring of named trade press and any
brokerage blogs/Substacks Phil adds to config/sources.yaml (§4). Free, no key, no
quota — and a better fit than a search API for "watch ~10 named publications."
Google Custom Search was considered and dropped: closed to new customers since
2025, sunsets Jan 2027.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import feedparser

from bpb.discovery.search_provider import CandidateRef, QuerySpec

logger = logging.getLogger(__name__)

_FIXTURE_ENTRY = {
    "link": "https://example.com/fixture-article",
    "title": "Fixture: How AI Insurance Is Changing Broker Conversations",
    "summary": "A fixture article summary mentioning AI insurance.",
    "published": None,
    "author": "Fixture Author",
}


class RssFeedProvider:
    """`feeds` is the list of {"name", "domain", "rss"} dicts from sources.yaml's
    trade_press/brokerage_blogs/newsletters sections. Feeds without an "rss" key
    are skipped (a source without a published feed just isn't reachable by this
    provider — Phil can add one with a feed, or Brave/Anthropic-hosted can be
    enabled to cover it)."""

    returns_full_content = False

    def __init__(
        self,
        feeds: list[dict[str, Any]],
        *,
        dry_run: bool = False,
        parse_fn: Callable[[str], Any] = feedparser.parse,
    ) -> None:
        self.feeds = [f for f in feeds if f.get("rss")]
        self.dry_run = dry_run
        self._parse_fn = parse_fn

    def discover(self, query_spec: QuerySpec) -> list[CandidateRef]:
        terms = [t.lower() for t in query_spec.terms]
        candidates: list[CandidateRef] = []

        for feed in self.feeds:
            domain = feed.get("domain")
            if query_spec.allowed_domains and domain not in query_spec.allowed_domains:
                continue

            entries = self._entries_for(feed)
            for entry in entries[: query_spec.max_results]:
                link = entry.get("link", "")
                if not link:
                    continue
                haystack = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()
                if terms and not any(t in haystack for t in terms):
                    continue
                candidates.append(
                    CandidateRef(
                        url=link,
                        title=entry.get("title"),
                        snippet=entry.get("summary"),
                        published_at=entry.get("published"),
                        author_hint=entry.get("author"),
                        source_domain=domain,
                    )
                )
        return candidates

    def _entries_for(self, feed: dict[str, Any]) -> list[dict[str, Any]]:
        if self.dry_run:
            return [dict(_FIXTURE_ENTRY, source=feed.get("domain"))]
        try:
            parsed = self._parse_fn(feed["rss"])
        except Exception:
            logger.warning("Failed to parse feed %s", feed.get("rss"), exc_info=True)
            return []
        return list(getattr(parsed, "entries", []) or [])
