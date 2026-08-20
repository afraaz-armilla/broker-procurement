"""HubSpot CRM logging (§10). Deliberately per-contact `search` -> `create`/
`update` rather than the batch/upsert endpoint, which has documented defects at
the time of writing (409 on the whole request when a contact exists;
properties silently not set on newly created records). Fine at our volume
(10-25/week). API version pinned in one place (`API_VERSION`) since HubSpot is
rolling out date-versioned paths.
"""

from __future__ import annotations

from typing import Any

from bpb.clients.base import BaseClient

NOTE_TO_CONTACT_ASSOCIATION_TYPE_ID = 202


class HubSpotClient(BaseClient):
    API_VERSION = "v3"

    def __init__(self, token: str, *, dry_run: bool = False) -> None:
        super().__init__(
            base_url="https://api.hubapi.com",
            dry_run=dry_run,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )

    def _path(self, suffix: str) -> str:
        return f"/crm/{self.API_VERSION}/{suffix}"

    def find_contact_by_email(self, email: str) -> dict[str, Any] | None:
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
            ]
        }
        data = self.request("POST", self._path("objects/contacts/search"), json=body)
        results = data.get("results", [])
        return results[0] if results else None

    def find_contact_by_name_and_company(
        self, first_name: str, last_name: str, company: str
    ) -> dict[str, Any] | None:
        filters = [
            {"propertyName": "firstname", "operator": "EQ", "value": first_name},
            {"propertyName": "lastname", "operator": "EQ", "value": last_name},
            {"propertyName": "company", "operator": "EQ", "value": company},
        ]
        body = {"filterGroups": [{"filters": filters}]}
        data = self.request("POST", self._path("objects/contacts/search"), json=body)
        results = data.get("results", [])
        return results[0] if results else None

    def create_contact(self, *, email: str | None, properties: dict[str, Any]) -> dict[str, Any]:
        props = dict(properties)
        if email:
            props["email"] = email
        return self.request("POST", self._path("objects/contacts"), json={"properties": props})

    def update_contact(self, contact_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "PATCH", self._path(f"objects/contacts/{contact_id}"), json={"properties": properties}
        )

    def upsert_contact(self, *, email: str | None, properties: dict[str, Any]) -> str:
        existing = (
            self.find_contact_by_email(email)
            if email
            else self.find_contact_by_name_and_company(
                properties.get("firstname", ""),
                properties.get("lastname", ""),
                properties.get("company", ""),
            )
        )
        if existing is not None:
            contact_id = existing["id"]
            self.update_contact(contact_id, properties)
            return contact_id
        created = self.create_contact(email=email, properties=properties)
        return created["id"]

    def create_note(self, *, body: str, contact_id: str, timestamp_ms: int) -> str:
        payload = {
            "properties": {"hs_note_body": body, "hs_timestamp": timestamp_ms},
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": NOTE_TO_CONTACT_ASSOCIATION_TYPE_ID,
                        }
                    ],
                }
            ],
        }
        data = self.request("POST", self._path("objects/notes"), json=payload)
        return data["id"]

    def _dry_run(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path.endswith("/contacts/search"):
            return {"results": []}
        if path.endswith("/objects/notes"):
            return {"id": "fixture-note-id"}
        if path.endswith("/objects/contacts"):
            return {
                "id": "fixture-contact-id",
                "properties": kwargs.get("json", {}).get("properties", {}),
            }
        if "/objects/contacts/" in path:
            return {"id": path.rsplit("/", 1)[-1], "properties": {}}
        return {"id": "fixture-id"}
