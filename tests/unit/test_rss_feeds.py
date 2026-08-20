from types import SimpleNamespace

from bpb.discovery.rss_feeds import RssFeedProvider
from bpb.discovery.search_provider import QuerySpec

FEEDS = [
    {"name": "Trade Press", "domain": "example.com", "rss": "https://example.com/feed"},
    {"name": "No Feed Blog", "domain": "nofeed.example"},  # no "rss" key — should be skipped
]


def _fake_parse(entries):
    def parse_fn(url):
        return SimpleNamespace(entries=entries)

    return parse_fn


def test_feeds_without_an_rss_url_are_skipped():
    provider = RssFeedProvider(FEEDS, parse_fn=_fake_parse([]))
    assert len(provider.feeds) == 1
    assert provider.feeds[0]["domain"] == "example.com"


def test_discover_filters_by_query_terms():
    entries = [
        {
            "link": "https://example.com/a",
            "title": "AI insurance is changing the market",
            "summary": "",
        },
        {"link": "https://example.com/b", "title": "Unrelated article about golf", "summary": ""},
    ]
    provider = RssFeedProvider(FEEDS, parse_fn=_fake_parse(entries))
    results = provider.discover(QuerySpec(terms=["ai insurance"], allowed_domains=[]))
    assert len(results) == 1
    assert results[0].url == "https://example.com/a"


def test_discover_with_no_terms_includes_everything():
    entries = [{"link": "https://example.com/a", "title": "Anything", "summary": ""}]
    provider = RssFeedProvider(FEEDS, parse_fn=_fake_parse(entries))
    results = provider.discover(QuerySpec(terms=[], allowed_domains=[]))
    assert len(results) == 1


def test_discover_respects_allowed_domains():
    entries = [{"link": "https://example.com/a", "title": "AI insurance", "summary": ""}]
    provider = RssFeedProvider(FEEDS, parse_fn=_fake_parse(entries))
    results = provider.discover(
        QuerySpec(terms=["ai insurance"], allowed_domains=["other.example"])
    )
    assert results == []


def test_discover_skips_entries_without_a_link():
    entries = [{"link": "", "title": "AI insurance", "summary": ""}]
    provider = RssFeedProvider(FEEDS, parse_fn=_fake_parse(entries))
    results = provider.discover(QuerySpec(terms=["ai insurance"], allowed_domains=[]))
    assert results == []


def test_dry_run_returns_fixture_without_calling_parse_fn():
    calls = []

    def tracking_parse_fn(url):
        calls.append(url)
        return SimpleNamespace(entries=[])

    provider = RssFeedProvider(FEEDS, dry_run=True, parse_fn=tracking_parse_fn)
    results = provider.discover(QuerySpec(terms=[], allowed_domains=[]))
    assert len(results) == 1
    assert calls == []  # never touched the network


def test_parse_failure_is_handled_gracefully():
    def failing_parse_fn(url):
        raise ValueError("boom")

    provider = RssFeedProvider(FEEDS, parse_fn=failing_parse_fn)
    results = provider.discover(QuerySpec(terms=[], allowed_domains=[]))
    assert results == []
