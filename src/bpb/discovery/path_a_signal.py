"""Path A orchestration (§4): provider(s) -> fetch/extract (unless the provider
already fused search+extract) -> LLM extraction -> firm resolution -> dedup ->
persist. Costs zero lookup credits — this is why the ledger's degrade mode falls
back to Path A first (see ledger/credits.py's `path_b_degraded`).
"""

from __future__ import annotations

import hashlib
import logging

from bpb import models
from bpb.clients.anthropic_client import AnthropicClient, ExtractionError
from bpb.config import Settings
from bpb.discovery import firm_resolver
from bpb.discovery.article_fetcher import fetch_article, is_linkedin_url
from bpb.discovery.path_b_coverage import find_existing_prospect
from bpb.discovery.rss_feeds import RssFeedProvider
from bpb.discovery.schemas import SignalExtraction
from bpb.discovery.search_provider import CandidateRef, QuerySpec, SearchProvider
from bpb.store.repo import Repo

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured signal records from articles or posts about AI "
    "insurance for a broker-prospecting pipeline. From the supplied text, "
    "identify: the author's name and title if a broker/insurance professional is "
    "bylined or clearly quoted as the source (null if none is identifiable), "
    "their firm name and a best-guess domain for it, the article title and "
    "published date if present, a 0-1 relevance score for how directly this "
    "concerns AI insurance or AI liability coverage, a one-sentence hook summary, "
    "and a short evidence quote taken VERBATIM from the supplied text — never "
    "invented — that a later outreach email could reference."
)


def _flatten_feeds(raw_sources: dict) -> list[dict]:
    feeds = list(raw_sources.get("trade_press", []) or [])
    feeds += raw_sources.get("brokerage_blogs", []) or []
    feeds += raw_sources.get("newsletters", []) or []
    return feeds


def build_providers(settings: Settings, *, dry_run: bool) -> list[SearchProvider]:
    from bpb.config import Secrets

    providers: list[SearchProvider] = []
    for name in settings.path_a.search_providers:
        if name == "rss":
            providers.append(RssFeedProvider(_flatten_feeds(settings.raw_sources), dry_run=dry_run))
        elif name == "brave":
            from bpb.discovery.brave import BraveSearchProvider

            providers.append(
                BraveSearchProvider(Secrets().brave_search_api_key or "", dry_run=dry_run)
            )
        elif name == "anthropic_hosted":
            from bpb.discovery.anthropic_hosted import AnthropicHostedProvider

            providers.append(
                AnthropicHostedProvider(
                    Secrets().anthropic_api_key or "",
                    model=settings.llm.extraction.model,
                    dry_run=dry_run,
                )
            )
        else:
            logger.warning("Unknown Path A search provider %r — skipping", name)
    return providers


def _dry_run_extraction(candidate: CandidateRef, meta_author: str | None) -> SignalExtraction:
    return SignalExtraction(
        author_name=meta_author or "Fixture Author",
        author_title="Broker",
        firm_name="Fixture Brokerage",
        firm_domain_guess=candidate.source_domain,
        article_url=candidate.url,
        article_title=candidate.title or "Fixture Article",
        published_date=candidate.published_at,
        ai_insurance_relevance=0.9,
        hook_summary="Fixture summary of an AI insurance article.",
        evidence_quote="Fixture evidence quote.",
    )


def _extract_from_text(
    llm: AnthropicClient,
    text: str,
    candidate: CandidateRef,
    meta_author: str | None,
    *,
    settings: Settings,
    dry_run: bool,
) -> SignalExtraction | None:
    fixture = _dry_run_extraction(candidate, meta_author) if dry_run else None
    try:
        return llm.extract_structured(
            model=settings.llm.extraction.model,
            system=EXTRACTION_SYSTEM_PROMPT,
            user_content=(
                f"Article URL: {candidate.url}\n"
                f"Known author hint: {meta_author or 'none'}\n\n"
                f"Text:\n{text}"
            ),
            schema_model=SignalExtraction,
            max_tokens=settings.llm.extraction.max_tokens,
            dry_run_fixture=fixture,
        )
    except ExtractionError:
        logger.warning("Extraction failed for %s", candidate.url, exc_info=True)
        return None


def discover_signals(
    repo: Repo, *, settings: Settings, llm: AnthropicClient, dry_run: bool = False
) -> dict:
    providers = build_providers(settings, dry_run=dry_run)
    query_terms = settings.raw_sources.get("query_terms", [])
    allowed_domains = [f["domain"] for f in _flatten_feeds(settings.raw_sources) if f.get("domain")]
    query_spec = QuerySpec(terms=query_terms, allowed_domains=allowed_domains, max_results=25)

    target_firms = repo.list_firms()
    existing_hashes = {s.url_hash for s in repo.list_signals()}

    stats = {
        "candidates_seen": 0,
        "signals_created": 0,
        "duplicates": 0,
        "below_relevance": 0,
        "unfetchable": 0,
        "off_target_firms": 0,
    }

    for provider in providers:
        for candidate in provider.discover(query_spec):
            stats["candidates_seen"] += 1
            url_hash = hashlib.sha256(candidate.url.encode()).hexdigest()
            if url_hash in existing_hashes:
                stats["duplicates"] += 1
                continue
            existing_hashes.add(url_hash)

            extraction = _resolve_extraction(
                provider, candidate, llm, settings=settings, dry_run=dry_run
            )
            if extraction is None:
                stats["unfetchable"] += 1
                continue
            if extraction.ai_insurance_relevance < settings.path_a.min_relevance:
                stats["below_relevance"] += 1
                continue

            firm, is_off_target = _resolve_firm(repo, extraction, target_firms)
            if is_off_target:
                stats["off_target_firms"] += 1

            prospect = None
            if extraction.author_name:
                linkedin_url = candidate.url if is_linkedin_url(candidate.url) else None
                prospect = find_existing_prospect(repo, firm.id, extraction.author_name)
                if prospect is None:
                    prospect = repo.upsert_prospect(
                        models.Prospect(
                            firm_id=firm.id,
                            full_name=extraction.author_name,
                            title=extraction.author_title,
                            source_path="A",
                            linkedin_url=linkedin_url,
                        )
                    )
                elif linkedin_url and not prospect.linkedin_url:
                    prospect.linkedin_url = linkedin_url
                    repo.upsert_prospect(prospect)

            signal = models.Signal(
                firm_id=firm.id,
                prospect_id=prospect.id if prospect else None,
                url=candidate.url,
                url_hash=url_hash,
                publication=candidate.source_domain,
                article_title=extraction.article_title,
                hook_summary=extraction.hook_summary,
                evidence_quote=extraction.evidence_quote,
                relevance=extraction.ai_insurance_relevance,
                raw_json=extraction.model_dump_json(),
            )
            repo.add_signal(signal)
            stats["signals_created"] += 1

            firm.coverage_state = "path_a_hit"
            repo.upsert_firm(firm)

    repo.flush()
    return stats


def _resolve_extraction(
    provider: SearchProvider,
    candidate: CandidateRef,
    llm: AnthropicClient,
    *,
    settings: Settings,
    dry_run: bool,
) -> SignalExtraction | None:
    if provider.returns_full_content:
        return candidate.extracted

    if is_linkedin_url(candidate.url):
        # Never fetched — search-snippet-only, per the LinkedIn compliance boundary.
        text = candidate.snippet or candidate.title or ""
        meta_author = candidate.author_hint
        if not text:
            return None
        return _extract_from_text(
            llm, text, candidate, meta_author, settings=settings, dry_run=dry_run
        )

    article = fetch_article(
        candidate.url,
        fallback_snippet=candidate.snippet or "",
        min_chars=settings.path_a.min_article_chars,
        dry_run=dry_run,
    )
    if article is None:
        return None
    meta_author = article.author or candidate.author_hint
    return _extract_from_text(
        llm, article.text, candidate, meta_author, settings=settings, dry_run=dry_run
    )


def _resolve_firm(
    repo: Repo, extraction: SignalExtraction, target_firms: list[models.Firm]
) -> tuple[models.Firm, bool]:
    firm = firm_resolver.resolve_firm(
        extraction.firm_name or "", extraction.firm_domain_guess, target_firms
    )
    if firm is not None:
        return firm, False

    firm = repo.upsert_firm(
        models.Firm(
            name=extraction.firm_name or "Unknown firm",
            domain=extraction.firm_domain_guess,
            on_target_list=False,
        )
    )
    target_firms.append(firm)
    return firm, True
