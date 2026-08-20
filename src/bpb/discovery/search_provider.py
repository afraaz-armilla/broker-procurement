"""The provider seam that keeps Path A's discovery mechanism swappable via config
(`settings.yaml`'s `path_a.search_providers`) without touching the rest of the
pipeline (§4). `returns_full_content` is the branch point: providers that only
return snippets/links (rss, brave, google_cse) get routed through
article_fetcher.py + an LLM extraction call by discovery/path_a_signal.py; a
provider that fuses search+read+extract in one call (anthropic_hosted) sets it
True and populates `extracted` directly, skipping the fetch step entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from bpb.discovery.schemas import SignalExtraction


@dataclass(frozen=True)
class QuerySpec:
    terms: list[str]
    allowed_domains: list[str]
    max_results: int = 10


@dataclass(frozen=True)
class CandidateRef:
    url: str
    title: str | None = None
    snippet: str | None = None
    published_at: str | None = None
    author_hint: str | None = None
    source_domain: str | None = None
    # Only set when the owning provider's `returns_full_content` is True.
    extracted: SignalExtraction | None = None


class SearchProvider(Protocol):
    @property
    def returns_full_content(self) -> bool: ...

    def discover(self, query_spec: QuerySpec) -> list[CandidateRef]: ...
