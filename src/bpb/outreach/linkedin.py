"""LinkedIn is a peer channel to email, but eligibility is narrower and neither
G3 (email resolution) nor G4 (verification) applies to it — see §8. Because of
that, a top-tier Path A prospect whose email can't be resolved or verified
doesn't have to be a dead end; they fall through to LinkedIn-only.

No LinkedIn API, no automation — this only builds copy-ready text plus the
profile URL for Phil to send by hand from his own account.
"""

from __future__ import annotations

from bpb import models


def is_linkedin_eligible(prospect: models.Prospect) -> bool:
    """Path A source, top-tier (band 1), has a profile URL."""
    return prospect.source_path == "A" and prospect.role_band == 1 and bool(prospect.linkedin_url)


def build_linkedin_payload(message: str, linkedin_url: str) -> str:
    return f"{message}\n\nProfile: {linkedin_url}"
