"""Shared HTTP client plumbing: retry/backoff, redacted logging, and — critically —
the dry-run boundary. `--dry-run` is enforced HERE, not by scattering `if dry_run`
checks through every call site: a BaseClient constructed with dry_run=True never
opens a socket; it returns each subclass's `_dry_run` fixture instead. That's what
lets `bpb validate-batch --dry-run` exercise the full pipeline in CI with zero
external spend.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_REDACT_KEYS = {"api_key", "apikey", "authorization", "x-api-key", "token", "access_token"}


def redact(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {k: ("***" if k.lower() in _REDACT_KEYS else v) for k, v in params.items()}


class ApiError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body[:300]}")


class RateLimitedError(httpx.TransportError):
    """Raised on a 429 so tenacity's retry (below) picks it up as retryable."""


class BaseClient:
    """Subclass and implement `_dry_run(method, path, **kwargs) -> dict` to add a
    new external service. `request()` is the only method that touches the network,
    and only when dry_run=False."""

    def __init__(
        self,
        *,
        base_url: str,
        dry_run: bool,
        timeout: float = 15.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.dry_run = dry_run
        self._client: httpx.Client | None = None
        if not dry_run:
            self._client = httpx.Client(base_url=base_url, timeout=timeout, headers=headers or {})

    def _dry_run(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError(
            f"{type(self).__name__} has no dry-run fixture for {method} {path}"
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self.dry_run:
            logger.info("dry_run_call", extra={"method": method, "path": path})
            return self._dry_run(method, path, **kwargs)

        assert self._client is not None
        logger.info(
            "http_call",
            extra={"method": method, "path": path, "params": redact(kwargs.get("params"))},
        )
        response = self._client.request(method, path, **kwargs)
        if response.status_code == 429:
            raise RateLimitedError(f"429 from {path}")
        if response.status_code >= 400:
            raise ApiError(response.status_code, response.text)
        return response.json()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> BaseClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
