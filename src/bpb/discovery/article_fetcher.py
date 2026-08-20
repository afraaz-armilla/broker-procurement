"""Fetch + extract stage for every non-hosted Path A provider (§4.3): robots.txt
check -> polite httpx fetch -> trafilatura extraction (clean text plus
author/title/date/sitename metadata — author name is the field Path A most
needs, and getting it from page metadata beats asking the LLM to guess it).
Paywalled/thin articles degrade to a snippet-only record rather than being
dropped outright; a bylined teaser is often enough to identify author + firm.

LinkedIn boundary (compliance-critical, see docs/compliance.md): this module
NEVER fetches linkedin.com — no login, no scraping, no automation. A LinkedIn
URL surfaced by a provider is only ever passed through as a profile link.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura

logger = logging.getLogger(__name__)

USER_AGENT = "ArmillaBrokerBot/1.0 (+https://www.armilla.ai)"
MAX_BODY_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 10.0

_robots_cache: dict[str, RobotFileParser | None] = {}


def is_linkedin_url(url: str) -> bool:
    return "linkedin.com" in urlparse(url).netloc.lower()


def _robots_allowed(url: str) -> bool:
    parsed = urlparse(url)
    host = f"{parsed.scheme}://{parsed.netloc}"
    if host not in _robots_cache:
        rp = RobotFileParser()
        rp.set_url(f"{host}/robots.txt")
        try:
            rp.read()
            _robots_cache[host] = rp
        except Exception:
            logger.warning("Could not read robots.txt for %s — treating as disallowed", host)
            _robots_cache[host] = None
    cached = _robots_cache[host]
    return cached is not None and cached.can_fetch(USER_AGENT, url)


@dataclass(frozen=True)
class FetchedArticle:
    url: str
    text: str
    author: str | None
    title: str | None
    published_date: str | None
    sitename: str | None
    is_snippet_only: bool


def fetch_article(
    url: str,
    *,
    fallback_snippet: str = "",
    min_chars: int = 600,
    dry_run: bool = False,
) -> FetchedArticle | None:
    if is_linkedin_url(url):
        return None  # compliance backstop — see module docstring

    if dry_run:
        text = fallback_snippet or "Fixture article body about AI insurance and broker coverage."
        return FetchedArticle(
            url=url, text=text, author="Fixture Author", title="Fixture Article",
            published_date=None, sitename="fixture.example", is_snippet_only=False,
        )

    if not _robots_allowed(url):
        logger.info("robots.txt disallows fetching %s", url)
        return _snippet_fallback(url, fallback_snippet)

    try:
        response = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Failed to fetch %s", url, exc_info=True)
        return _snippet_fallback(url, fallback_snippet)

    html = response.content[:MAX_BODY_BYTES]
    extracted_json = trafilatura.extract(html, output_format="json", with_metadata=True, url=url)
    if not extracted_json:
        return _snippet_fallback(url, fallback_snippet)

    data = json.loads(extracted_json)
    text = data.get("text") or ""
    author = data.get("author")
    title = data.get("title")
    published_date = data.get("date")
    sitename = data.get("sitename")

    if len(text) < min_chars:
        combined = "\n".join(p for p in (text, fallback_snippet) if p).strip()
        if not combined:
            return _snippet_fallback(url, fallback_snippet)
        return FetchedArticle(
            url=url, text=combined, author=author, title=title,
            published_date=published_date, sitename=sitename, is_snippet_only=True,
        )

    return FetchedArticle(
        url=url, text=text, author=author, title=title,
        published_date=published_date, sitename=sitename, is_snippet_only=False,
    )


def _snippet_fallback(url: str, fallback_snippet: str) -> FetchedArticle | None:
    if not fallback_snippet:
        return None
    return FetchedArticle(
        url=url, text=fallback_snippet, author=None, title=None,
        published_date=None, sitename=None, is_snippet_only=True,
    )
