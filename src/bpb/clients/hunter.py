"""Hunter — email-resolution fallback (domain-search primary, email-finder last
resort). https://hunter.io/api-documentation/v2

Credit accounting is response-derived, not per-call: domain-search charges 1
credit total per call ONLY if it returns at least one email (regardless of how
many, up to the page limit); email-finder charges 1 only on a hit. Assuming a
flat per-call charge would over-report spend and trigger false hard-stops (build
plan §11) — `credits_charged_for_*` below is what the caller uses to log the
*actual* charge on the Verification/Email side; the ledger's own `reserve()` still
writes a conservative worst-case BEFORE the call (see ledger/credits.py).
"""

from __future__ import annotations

from typing import Any

from bpb.clients.base import BaseClient


class HunterClient(BaseClient):
    def __init__(self, api_key: str, *, dry_run: bool = False) -> None:
        super().__init__(base_url="https://api.hunter.io/v2", dry_run=dry_run)
        self.api_key = api_key

    def domain_search(self, domain: str, *, limit: int = 10) -> dict[str, Any]:
        return self.request(
            "GET",
            "/domain-search",
            params={"domain": domain, "api_key": self.api_key, "limit": limit},
        )

    def email_finder(self, *, domain: str, first_name: str, last_name: str) -> dict[str, Any]:
        return self.request(
            "GET",
            "/email-finder",
            params={
                "domain": domain,
                "first_name": first_name,
                "last_name": last_name,
                "api_key": self.api_key,
            },
        )

    def get_account(self) -> dict[str, Any]:
        return self.request("GET", "/account", params={"api_key": self.api_key})

    @staticmethod
    def credits_charged_for_domain_search(result: dict[str, Any]) -> int:
        emails = result.get("data", {}).get("emails") or []
        return 1 if emails else 0

    @staticmethod
    def credits_charged_for_email_finder(result: dict[str, Any]) -> int:
        return 1 if result.get("data", {}).get("email") else 0

    def _dry_run(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        params = kwargs.get("params", {})
        if path == "/domain-search":
            domain = params.get("domain", "example.com")
            return {
                "data": {
                    "domain": domain,
                    "pattern": "{first}.{last}",
                    "accept_all": False,
                    "emails": [
                        {
                            "value": f"fixture.broker@{domain}",
                            "first_name": "Fixture",
                            "last_name": "Broker",
                            "position": "Vice President",
                        }
                    ],
                }
            }
        if path == "/email-finder":
            domain = params.get("domain", "example.com")
            first = params.get("first_name", "fixture")
            last = params.get("last_name", "broker")
            return {"data": {"email": f"{first}.{last}@{domain}".lower(), "score": 90}}
        if path == "/account":
            return {
                "data": {
                    "requests": {
                        "searches": {"available": 999},
                        "verifications": {"available": 999},
                    }
                }
            }
        return {}
