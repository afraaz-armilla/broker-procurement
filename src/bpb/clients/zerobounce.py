"""ZeroBounce — primary email verifier. https://www.zerobounce.net/docs/email-validation-api/

ZeroBounce does NOT charge a credit for a `status: "unknown"` result (the API
couldn't determine deliverability) — only for a determinate one. That's why credit
accounting lives here rather than being assumed 1-per-call by the ledger (§11 of
the build plan calls this out explicitly as a false-hard-stop risk).
"""

from __future__ import annotations

from typing import Any

from bpb.clients.base import BaseClient

DETERMINATE_STATUSES = {"valid", "invalid", "catch-all", "spamtrap", "abuse", "do_not_mail"}


class ZeroBounceClient(BaseClient):
    def __init__(self, api_key: str, *, dry_run: bool = False) -> None:
        super().__init__(base_url="https://api.zerobounce.net/v2", dry_run=dry_run)
        self.api_key = api_key

    def validate(self, email: str) -> dict[str, Any]:
        return self.request("GET", "/validate", params={"api_key": self.api_key, "email": email})

    def get_credits(self) -> int:
        data = self.request("GET", "/getcredits", params={"api_key": self.api_key})
        try:
            return int(data.get("Credits", -1))
        except (TypeError, ValueError):
            return -1

    def credits_charged_for(self, result: dict[str, Any]) -> int:
        return 1 if result.get("status") in DETERMINATE_STATUSES else 0

    def _dry_run(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path == "/validate":
            email = kwargs.get("params", {}).get("email", "")
            return {
                "address": email,
                "status": "valid",
                "sub_status": "",
                "account": email.split("@")[0] if "@" in email else "",
                "domain": email.split("@")[-1] if "@" in email else "",
                "smtp_provider": "fixture",
                "processed_at": "fixture",
            }
        if path == "/getcredits":
            return {"Credits": "999999"}
        return {}
