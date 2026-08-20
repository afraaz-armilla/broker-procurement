from bpb import models
from bpb.outreach.linkedin import build_linkedin_payload, is_linkedin_eligible


def _prospect(**kwargs) -> models.Prospect:
    defaults = dict(firm_id="f1", full_name="Jane Doe")
    defaults.update(kwargs)
    return models.Prospect(**defaults)


def test_eligible_when_path_a_band_1_with_linkedin_url():
    p = _prospect(source_path="A", role_band=1, linkedin_url="https://linkedin.com/in/jane")
    assert is_linkedin_eligible(p) is True


def test_ineligible_when_path_b():
    p = _prospect(source_path="B", role_band=1, linkedin_url="https://linkedin.com/in/jane")
    assert is_linkedin_eligible(p) is False


def test_ineligible_when_not_band_1():
    p = _prospect(source_path="A", role_band=2, linkedin_url="https://linkedin.com/in/jane")
    assert is_linkedin_eligible(p) is False


def test_ineligible_without_linkedin_url():
    p = _prospect(source_path="A", role_band=1, linkedin_url=None)
    assert is_linkedin_eligible(p) is False


def test_build_linkedin_payload_includes_profile_url():
    payload = build_linkedin_payload("Hi Jane, saw your piece.", "https://linkedin.com/in/jane")
    assert "Hi Jane, saw your piece." in payload
    assert "https://linkedin.com/in/jane" in payload
