"""Anthropic Messages API wrapper for schema-validated structured output — used
by Path A extraction (phase 6) and outreach drafting (phase 7). Deliberately
built on the plain, stable `messages.create` call plus a "reply with only this
JSON schema" instruction + pydantic validation, rather than any hypothetical
structured-output parameter, since this was built without a live call against
the current API to confirm such a parameter's exact shape today. One retry,
feeding the validation error back to the model, before giving up — the same
pattern outreach/claim_validator.py uses downstream in phase 7.
"""

from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ExtractionError(RuntimeError):
    pass


class AnthropicClient:
    def __init__(self, api_key: str, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self._client = None
        if not dry_run:
            import anthropic

            self._client = anthropic.Anthropic(api_key=api_key)

    def extract_structured(
        self,
        *,
        model: str,
        system: str,
        user_content: str,
        schema_model: type[T],
        max_tokens: int = 1500,
        dry_run_fixture: T | None = None,
    ) -> T:
        if self.dry_run or self._client is None:
            if dry_run_fixture is None:
                raise ExtractionError(f"No dry-run fixture supplied for {schema_model.__name__}")
            return dry_run_fixture

        schema_instructions = (
            f"{system}\n\nRespond with ONLY a single JSON object matching this schema — "
            f"no prose, no markdown code fences:\n{schema_model.model_json_schema()}"
        )
        first = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=schema_instructions,
            messages=[{"role": "user", "content": user_content}],
        )
        first_text = _text_of(first)
        try:
            return schema_model.model_validate_json(_strip_fences(first_text))
        except Exception as exc:
            # `except ... as name` unbinds `name` once the block exits (Python 3
            # semantics) — capture the message now, it's needed after this block.
            first_error_message = str(exc)
            logger.warning(
                "LLM output failed schema validation, retrying once: %s", first_error_message
            )

        retry = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=schema_instructions,
            messages=[
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": first_text},
                {
                    "role": "user",
                    "content": f"That response failed validation: {first_error_message}. "
                    "Reply again with ONLY the corrected JSON object.",
                },
            ],
        )
        retry_text = _text_of(retry)
        try:
            return schema_model.model_validate_json(_strip_fences(retry_text))
        except Exception as retry_error:
            raise ExtractionError(
                f"LLM output failed schema validation twice: {retry_error}"
            ) from retry_error


def _text_of(message) -> str:
    return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -len("```")]
        if t.lower().startswith("json"):
            t = t[4:]
    return t.strip()
