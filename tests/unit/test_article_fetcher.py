import json

import httpx
import respx

from bpb.discovery import article_fetcher
from bpb.discovery.article_fetcher import fetch_article, is_linkedin_url


def test_is_linkedin_url():
    assert is_linkedin_url("https://www.linkedin.com/posts/jane-doe_ai-insurance")
    assert not is_linkedin_url("https://example.com/linkedin-mentioned-in-title")


def test_never_fetches_linkedin_even_live(monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("must never fetch linkedin.com")

    monkeypatch.setattr(httpx, "get", fail_if_called)
    result = fetch_article("https://www.linkedin.com/posts/jane-doe", fallback_snippet="hi")
    assert result is None


def test_dry_run_returns_fixture_without_network(monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("dry-run must not touch the network")

    monkeypatch.setattr(httpx, "get", fail_if_called)
    result = fetch_article("https://example.com/article", dry_run=True)
    assert result is not None
    assert result.is_snippet_only is False


def test_robots_disallowed_falls_back_to_snippet(monkeypatch):
    monkeypatch.setattr(article_fetcher, "_robots_allowed", lambda url: False)
    result = fetch_article("https://example.com/article", fallback_snippet="a teaser")
    assert result is not None
    assert result.is_snippet_only is True
    assert result.text == "a teaser"


def test_robots_disallowed_no_snippet_returns_none(monkeypatch):
    monkeypatch.setattr(article_fetcher, "_robots_allowed", lambda url: False)
    result = fetch_article("https://example.com/article", fallback_snippet="")
    assert result is None


@respx.mock
def test_full_length_article_is_not_snippet_only(monkeypatch):
    monkeypatch.setattr(article_fetcher, "_robots_allowed", lambda url: True)
    long_text = "AI insurance is reshaping broker conversations. " * 30  # well over 600 chars
    monkeypatch.setattr(
        article_fetcher.trafilatura,
        "extract",
        lambda *a, **k: json.dumps(
            {
                "text": long_text,
                "author": "Jane Doe",
                "title": "AI Insurance",
                "date": "2026-01-01",
                "sitename": "example.com",
            }
        ),
    )
    respx.get("https://example.com/article").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )

    result = fetch_article("https://example.com/article", min_chars=600)
    assert result is not None
    assert result.is_snippet_only is False
    assert result.author == "Jane Doe"


@respx.mock
def test_thin_extraction_degrades_to_snippet_only_with_teaser_appended(monkeypatch):
    monkeypatch.setattr(article_fetcher, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(
        article_fetcher.trafilatura,
        "extract",
        lambda *a, **k: json.dumps(
            {
                "text": "Paywall teaser only.",
                "author": "Jane Doe",
                "title": None,
                "date": None,
                "sitename": None,
            }
        ),
    )
    respx.get("https://example.com/paywalled").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )

    result = fetch_article(
        "https://example.com/paywalled", fallback_snippet="RSS summary text", min_chars=600
    )
    assert result is not None
    assert result.is_snippet_only is True
    assert "Paywall teaser only." in result.text
    assert "RSS summary text" in result.text
    assert result.author == "Jane Doe"


@respx.mock
def test_extraction_failure_falls_back_to_snippet(monkeypatch):
    monkeypatch.setattr(article_fetcher, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(article_fetcher.trafilatura, "extract", lambda *a, **k: None)
    respx.get("https://example.com/unparseable").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )

    result = fetch_article("https://example.com/unparseable", fallback_snippet="teaser")
    assert result is not None
    assert result.is_snippet_only is True
    assert result.text == "teaser"


@respx.mock
def test_http_error_falls_back_to_snippet(monkeypatch):
    monkeypatch.setattr(article_fetcher, "_robots_allowed", lambda url: True)
    respx.get("https://example.com/404").mock(return_value=httpx.Response(404))

    result = fetch_article("https://example.com/404", fallback_snippet="teaser")
    assert result is not None
    assert result.is_snippet_only is True
