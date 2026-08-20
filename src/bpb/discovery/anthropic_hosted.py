"""Optional, premium Path A provider: Anthropic's hosted web-search tool, which
searches, reads, and extracts in one call (~$10/1k searches) — the only provider
where `returns_full_content` is True. Not the default (RSS is, at $0).

CAVEAT — worth a recheck before enabling: the hosted web-search tool's exact type
string (passed as `tools=[{"type": ..., "name": "web_search", ...}]` in the
Messages API) changes as Anthropic revs it. The default below was confirmed
against the `anthropic` Python SDK's own installed type stubs at build time
(0.125.0 -> `WebSearchTool20260209Param`, type `"web_search_20260209"`) rather
than a live API call, since this was built under a no-external-spend constraint;
no actual search request was made against Anthropic's API to verify it end to
end. `tool_type` is a constructor arg (not hardcoded) specifically so a future
SDK bump doesn't need a code change here, just an updated default/config value.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from bpb.discovery.schemas import SignalExtraction
from bpb.discovery.search_provider import CandidateRef, QuerySpec

logger = logging.getLogger(__name__)

_FIXTURE_EXTRACTION = SignalExtraction(
    author_name="Fixture Hosted Author",
    author_title="Broker",
    firm_name="Fixture Brokerage",
    firm_domain_guess="example.com",
    article_url="https://example.com/fixture-hosted-article",
    article_title="Fixture Hosted Article",
    ai_insurance_relevance=0.9,
    hook_summary="Fixture summary from the hosted search+extract provider.",
    evidence_quote="Fixture evidence quote from the hosted provider.",
)


class AnthropicHostedProvider:
    returns_full_content = True

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        # Verified against anthropic SDK 0.125.0's own type stubs at build time —
        # still worth a quick recheck before enabling if much time has passed.
        tool_type: str = "web_search_20260209",
        dry_run: bool = False,
    ) -> None:
        self.dry_run = dry_run
        self.model = model
        self.tool_type = tool_type
        self._client = None
        if not dry_run:
            import anthropic

            self._client = anthropic.Anthropic(api_key=api_key)

    def discover(self, query_spec: QuerySpec) -> list[CandidateRef]:
        if self.dry_run or self._client is None:
            return [
                CandidateRef(
                    url=_FIXTURE_EXTRACTION.article_url,
                    title=_FIXTURE_EXTRACTION.article_title,
                    extracted=_FIXTURE_EXTRACTION,
                )
            ]

        site_scope = ", ".join(query_spec.allowed_domains)
        prompt = (
            f"Search for recent articles or posts about: {', '.join(query_spec.terms)}. "
            f"Restrict to these publications/domains: {site_scope}. "
            f"For each distinct relevant result, extract a JSON object matching this schema: "
            f"{json.dumps(SignalExtraction.model_json_schema())}. "
            f"Reply with a JSON array of such objects, nothing else."
        )
        tool_param: dict[str, Any] = {
            "type": self.tool_type,
            "name": "web_search",
            "max_uses": query_spec.max_results,
            "allowed_domains": query_spec.allowed_domains,
        }
        message = self._client.messages.create(
            model=self.model,
            max_tokens=4000,
            tools=[tool_param],  # type: ignore[list-item]  # see module CAVEAT
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in message.content)
        try:
            records = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Anthropic hosted search returned non-JSON output; skipping this run")
            return []

        candidates = []
        for record in records:
            try:
                extraction = SignalExtraction.model_validate(record)
            except Exception:
                logger.warning(
                    "Hosted-search record failed schema validation, skipping", exc_info=True
                )
                continue
            candidates.append(
                CandidateRef(
                    url=extraction.article_url,
                    title=extraction.article_title,
                    extracted=extraction,
                )
            )
        return candidates
