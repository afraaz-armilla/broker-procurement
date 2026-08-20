"""Slack Web API — the approval channel (§9). Reaction-driven, not buttons:
buttons need a public inbound webhook/server to receive interaction payloads,
which breaks the scheduled-batch model; reactions need no inbound infrastructure
at all, just outbound calls this client already makes.

Slack's API returns HTTP 200 even on a logical failure (`{"ok": false, "error":
...}`), so every call here is checked and raises SlackApiError rather than
silently returning a failure payload.
"""

from __future__ import annotations

from typing import Any

from bpb.clients.base import BaseClient


class SlackApiError(RuntimeError):
    def __init__(self, error: str):
        self.error = error
        super().__init__(f"Slack API error: {error}")


class SlackClient(BaseClient):
    def __init__(self, bot_token: str, *, dry_run: bool = False) -> None:
        super().__init__(
            base_url="https://slack.com/api",
            dry_run=dry_run,
            headers={
                "Authorization": f"Bearer {bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )

    def post_message(self, *, channel: str, text: str) -> dict[str, Any]:
        return self._checked("POST", "/chat.postMessage", json={"channel": channel, "text": text})

    def update_message(self, *, channel: str, ts: str, text: str) -> dict[str, Any]:
        return self._checked(
            "POST", "/chat.update", json={"channel": channel, "ts": ts, "text": text}
        )

    def get_reactions(self, *, channel: str, ts: str) -> list[dict[str, Any]]:
        data = self._checked(
            "GET", "/reactions.get", params={"channel": channel, "timestamp": ts, "full": "true"}
        )
        return data.get("message", {}).get("reactions", [])

    def get_replies(self, *, channel: str, ts: str) -> list[dict[str, Any]]:
        data = self._checked("GET", "/conversations.replies", params={"channel": channel, "ts": ts})
        return data.get("messages", [])

    def _checked(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        data = self.request(method, path, **kwargs)
        if not data.get("ok", True):
            raise SlackApiError(data.get("error", "unknown_error"))
        return data

    def _dry_run(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path == "/chat.postMessage":
            return {"ok": True, "channel": kwargs["json"]["channel"], "ts": "1700000000.000001"}
        if path == "/chat.update":
            return {"ok": True, "channel": kwargs["json"]["channel"], "ts": kwargs["json"]["ts"]}
        if path == "/reactions.get":
            return {"ok": True, "message": {"reactions": []}}
        if path == "/conversations.replies":
            return {"ok": True, "messages": []}
        return {"ok": True}
