"""Apollo — Path B targeting (free) and email-reveal fallback (1 credit/hit).
https://docs.apollo.io/reference

`match_person` hardcodes `reveal_phone_number: false` — not exposed as a
parameter at all, so no call site can accidentally opt into an 8-credit phone
reveal for a field this bot never uses (~6% of the monthly Apollo budget for
nothing, per the build plan's §5).
"""

from __future__ import annotations

from typing import Any

from bpb.clients.base import BaseClient


class ApolloClient(BaseClient):
    def __init__(self, api_key: str, *, dry_run: bool = False) -> None:
        super().__init__(
            base_url="https://api.apollo.io/api/v1",
            dry_run=dry_run,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
        )
        self.api_key = api_key

    def search_people(
        self,
        *,
        titles: list[str],
        locations: list[str],
        organization_domains: list[str],
        per_page: int = 25,
    ) -> list[dict[str, Any]]:
        """Free targeting endpoint — no email, no credits charged."""
        body = {
            "person_titles": titles,
            "person_locations": locations,
            "q_organization_domains": organization_domains,
            "per_page": per_page,
        }
        data = self.request("POST", "/mixed_people/api_search", json=body)
        return data.get("people", [])

    def match_person(self, *, first_name: str, last_name: str, domain: str) -> dict[str, Any]:
        """Email-reveal fallback — costs 1 lead credit. Phone reveal is always off."""
        body = {
            "first_name": first_name,
            "last_name": last_name,
            "domain": domain,
            "reveal_personal_emails": True,
            "reveal_phone_number": False,
        }
        return self.request("POST", "/people/match", json=body)

    def get_credit_usage(self) -> dict[str, Any]:
        return self.request("GET", "/usage_stats/credit_usage_stats")

    def _dry_run(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path == "/mixed_people/api_search":
            domains = kwargs.get("json", {}).get("q_organization_domains", [])
            domain = domains[0] if domains else "example.com"
            return {
                "people": [
                    {
                        "first_name": "Fixture",
                        "last_name": "Broker",
                        "title": "Vice President",
                        "linkedin_url": None,
                        "organization": {"primary_domain": domain},
                    }
                ]
            }
        if path == "/people/match":
            first = kwargs.get("json", {}).get("first_name", "fixture")
            last = kwargs.get("json", {}).get("last_name", "broker")
            domain = kwargs.get("json", {}).get("domain", "example.com")
            return {
                "person": {
                    "email": f"{first}.{last}@{domain}".lower(),
                    "email_status": "verified",
                }
            }
        if path == "/usage_stats/credit_usage_stats":
            return {"lead_credit": {"remaining": 999, "consumed": 0}}
        return {}
