"""Optional Path A provider: Brave Search API. Not the default (RSS is) — enable
via `path_a.search_providers: ["rss", "brave"]` in settings.yaml when Phil wants
broader coverage (blogs/Substacks/LinkedIn-surfaced posts RSS misses) than the
free tier's $5/month credit comfortably covers at this volume.
https://api-dashboard.search.brave.com/app/documentation/web-search/get-started
"""

from __future__ import annotations

from typing import Any

from bpb.clients.base import BaseClient
from bpb.discovery.search_provider import CandidateRef, QuerySpec


class BraveSearchProvider(BaseClient):
    returns_full_content = False

    def __init__(self, api_key: str, *, dry_run: bool = False) -> None:
        super().__init__(
            base_url="https://api.search.brave.com/res/v1",
            dry_run=dry_run,
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        )

    def discover(self, query_spec: QuerySpec) -> list[CandidateRef]:
        site_filter = " OR ".join(f"site:{d}" for d in query_spec.allowed_domains)
        candidates: list[CandidateRef] = []
        for term in query_spec.terms or [""]:
            query = f"{term} {site_filter}".strip()
            data = self.request(
                "GET", "/web/search", params={"q": query, "count": query_spec.max_results}
            )
            for result in data.get("web", {}).get("results", []):
                candidates.append(
                    CandidateRef(
                        url=result.get("url", ""),
                        title=result.get("title"),
                        snippet=result.get("description"),
                        published_at=result.get("age"),
                        source_domain=result.get("profile", {}).get("long_name"),
                    )
                )
        return candidates

    def _dry_run(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "web": {
                "results": [
                    {
                        "url": "https://example.com/fixture-brave-result",
                        "title": "Fixture Brave Result",
                        "description": "A fixture search-result snippet.",
                        "age": None,
                        "profile": {"long_name": "example.com"},
                    }
                ]
            }
        }
