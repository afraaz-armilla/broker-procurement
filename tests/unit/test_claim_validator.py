from bpb import models
from bpb.outreach.claim_validator import validate_claims

KNOWN_PUBLICATIONS = ["Insurance Business", "The Insurer"]


def _signal(**kwargs) -> models.Signal:
    defaults = dict(
        firm_id="f1",
        url="https://example.com/a",
        url_hash="h1",
        publication="Insurance Business",
        article_title="AI Insurance Is Here",
        evidence_quote="AI insurance is reshaping how brokers think about coverage.",
    )
    defaults.update(kwargs)
    return models.Signal(**defaults)


def test_no_signal_and_no_quotes_passes():
    result = validate_claims(
        draft_subject="Quick intro",
        draft_body="Hi Jane, would love to connect about AI insurance.",
        signal=None,
        known_publications=KNOWN_PUBLICATIONS,
    )
    assert result.passed is True


def test_no_signal_but_draft_quotes_something_fails():
    result = validate_claims(
        draft_subject="Re: your piece",
        draft_body='Loved this line: "AI insurance is reshaping the market."',
        signal=None,
        known_publications=KNOWN_PUBLICATIONS,
    )
    assert result.passed is False
    assert result.violations[0].kind == "quote_without_signal"


def test_quote_matching_evidence_verbatim_passes():
    signal = _signal()
    result = validate_claims(
        draft_subject="Re: AI insurance",
        draft_body='Loved this: "AI insurance is reshaping how brokers think about coverage."',
        signal=signal,
        known_publications=KNOWN_PUBLICATIONS,
    )
    assert result.passed is True


def test_quote_not_matching_evidence_fails():
    signal = _signal()
    result = validate_claims(
        draft_subject="Re: AI insurance",
        draft_body='Loved this: "something the source never actually said."',
        signal=signal,
        known_publications=KNOWN_PUBLICATIONS,
    )
    assert result.passed is False
    assert result.violations[0].kind == "quote_not_in_evidence"


def test_correct_publication_reference_passes():
    signal = _signal(publication="Insurance Business")
    result = validate_claims(
        draft_subject="Re: your piece",
        draft_body="Saw your piece in Insurance Business about AI coverage.",
        signal=signal,
        known_publications=KNOWN_PUBLICATIONS,
    )
    assert result.passed is True


def test_wrong_publication_reference_fails():
    signal = _signal(publication="Insurance Business")
    result = validate_claims(
        draft_subject="Re: your piece",
        draft_body="Saw your piece in The Insurer about AI coverage.",
        signal=signal,
        known_publications=KNOWN_PUBLICATIONS,
    )
    assert result.passed is False
    assert result.violations[0].kind == "wrong_publication"


def test_quote_matching_case_and_diacritic_insensitive():
    signal = _signal(evidence_quote="Ähmed said AI Insurance changes everything.")
    result = validate_claims(
        draft_subject="Hi",
        draft_body='He told me: "ahmed said ai insurance changes everything."',
        signal=signal,
        known_publications=KNOWN_PUBLICATIONS,
    )
    assert result.passed is True
