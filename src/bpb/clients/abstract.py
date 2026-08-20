"""Abstract API — fallback email verifier, used only when ZeroBounce is inconclusive
(`unknown`). https://www.abstractapi.com/api/email-verification-validation-api
"""

from __future__ import annotations

from typing import Any

from bpb.clients.base import BaseClient


class AbstractEmailClient(BaseClient):
    def __init__(self, api_key: str, *, dry_run: bool = False) -> None:
        super().__init__(base_url="https://emailvalidation.abstractapi.com/v1", dry_run=dry_run)
        self.api_key = api_key

    def validate(self, email: str) -> dict[str, Any]:
        return self.request("GET", "/", params={"api_key": self.api_key, "email": email})

    def _dry_run(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        email = kwargs.get("params", {}).get("email", "")
        return {
            "email": email,
            "deliverability": "DELIVERABLE",
            "quality_score": "0.90",
            "is_valid_format": {"value": True},
            "is_smtp_valid": {"value": True},
        }
