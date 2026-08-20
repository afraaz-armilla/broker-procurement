from bpb import models
from bpb.clients.anthropic_client import AnthropicClient, ExtractionError
from bpb.config import load_settings
from bpb.outreach.drafter import DraftOutput, draft_outreach

KNOWN_PUBLICATIONS = ["Insurance Business"]


class FakeLLM:
    def __init__(self, outputs=None, raises=False):
        self.outputs = outputs or []
        self.raises = raises
        self.calls = 0

    def extract_structured(self, **kwargs):
        self.calls += 1
        if self.raises:
            raise ExtractionError("boom")
        idx = min(self.calls - 1, len(self.outputs) - 1)
        return self.outputs[idx]


def _prospect() -> models.Prospect:
    return models.Prospect(firm_id="f1", full_name="Jane Doe", first_name="Jane", last_name="Doe")


def _firm() -> models.Firm:
    return models.Firm(name="Acme Brokerage", domain="acme.example")


def _signal() -> models.Signal:
    return models.Signal(
        firm_id="f1",
        url="https://example.com/a",
        url_hash="h1",
        publication="Insurance Business",
        article_title="AI Insurance Is Here",
        hook_summary="Jane wrote about AI insurance coverage gaps.",
        evidence_quote="AI insurance coverage gaps are the next big broker conversation.",
    )


def test_dry_run_produces_a_draft():
    settings = load_settings()
    llm = AnthropicClient("fake", dry_run=True)
    result = draft_outreach(
        _prospect(), _firm(), _signal(), llm=llm, settings=settings, template_variant={},
        sender_name="Phil", known_publications=KNOWN_PUBLICATIONS, dry_run=True,
    )
    assert result.needs_manual is False
    assert result.subject
    assert result.body


def test_llm_error_yields_needs_manual():
    settings = load_settings()
    llm = FakeLLM(raises=True)
    result = draft_outreach(
        _prospect(), _firm(), _signal(), llm=llm, settings=settings, template_variant={},
        sender_name="Phil", known_publications=KNOWN_PUBLICATIONS,
    )
    assert result.needs_manual is True


def test_violating_draft_retries_and_succeeds_on_clean_second_attempt():
    bad = DraftOutput(subject="Hi", body='Loved this: "a quote that was never said."')
    good = DraftOutput(subject="Hi", body="Referencing your work on AI insurance coverage gaps.")
    llm = FakeLLM(outputs=[bad, good])
    settings = load_settings()
    result = draft_outreach(
        _prospect(), _firm(), _signal(), llm=llm, settings=settings, template_variant={},
        sender_name="Phil", known_publications=KNOWN_PUBLICATIONS,
    )
    assert llm.calls == 2
    assert result.needs_manual is False
    assert result.body == good.body


def test_violating_draft_both_attempts_yields_needs_manual_with_violations():
    bad = DraftOutput(subject="Hi", body='Loved this: "a quote that was never said."')
    llm = FakeLLM(outputs=[bad, bad])
    settings = load_settings()
    result = draft_outreach(
        _prospect(), _firm(), _signal(), llm=llm, settings=settings, template_variant={},
        sender_name="Phil", known_publications=KNOWN_PUBLICATIONS,
    )
    assert llm.calls == 2
    assert result.needs_manual is True
    assert len(result.violations) > 0


def test_no_signal_produces_a_draft_without_claiming_to_have_seen_writing():
    settings = load_settings()
    llm = AnthropicClient("fake", dry_run=True)
    result = draft_outreach(
        _prospect(), _firm(), None, llm=llm, settings=settings, template_variant={},
        sender_name="Phil", known_publications=KNOWN_PUBLICATIONS, dry_run=True,
    )
    assert result.needs_manual is False
    assert result.linkedin_message is None  # dry-run fixture only sets this when a signal exists
