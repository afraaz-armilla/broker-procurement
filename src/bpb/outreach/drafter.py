"""G5: outreach drafting (§8). Combines Phil's message-sequence template variant
(config/TEMPLATE_message_sequence.yaml — may still be blank if he hasn't filled
it in) with the prospect's signal hook into a single LLM call, then runs the
result through claim_validator.py; on failure, one retry with the violation fed
back, then `needs_manual`. Output is plain paste-ready text — there is no
automated send, so no tracking pixels, no unsubscribe footer, no HTML.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from jinja2 import Template
from pydantic import BaseModel

from bpb import models
from bpb.clients.anthropic_client import AnthropicClient, ExtractionError
from bpb.config import Settings
from bpb.outreach.claim_validator import ClaimViolation, validate_claims

logger = logging.getLogger(__name__)


class DraftOutput(BaseModel):
    subject: str
    body: str
    linkedin_message: str | None = None


@dataclass
class DraftResult:
    subject: str | None
    body: str | None
    linkedin_message: str | None
    needs_manual: bool
    violations: list[ClaimViolation] = field(default_factory=list)


SYSTEM_PROMPT_TEMPLATE = (
    "You draft outreach on behalf of {sender_name} for a broker-prospecting "
    "campaign, following Phil's voice and constraints exactly.\n\n"
    "Constraints: at most {max_words} words in the body, exactly {cta_count} "
    "call to action, no superlatives.\n\n"
    "Hard rule: this text will be pasted directly into an email or LinkedIn "
    "message by a human — no HTML, no tracking pixel or unsubscribe language, "
    "no automated-campaign phrasing.\n\n"
    "Hard rule: do not reference any fact, quote, article title, or publication "
    "that is not explicitly provided to you in the recipient context below. If "
    "no signal is provided, do not claim to have seen the recipient's writing."
)


def _render(template_str: str | None, **context: str) -> str:
    if not template_str:
        return ""
    return Template(template_str).render(**context)


def _build_user_content(
    prospect: models.Prospect,
    firm: models.Firm,
    signal: models.Signal | None,
    step: dict,
    *,
    sender_name: str,
) -> str:
    context: dict[str, str] = {
        "first_name": prospect.first_name or prospect.full_name.split()[0],
        "firm_name": firm.name,
        "hook_summary": (signal.hook_summary if signal else "") or "",
        "article_title": (signal.article_title if signal else "") or "",
        "publication": (signal.publication if signal else "") or "",
        "sender_name": sender_name,
    }
    subject_template = _render(step.get("subject_template"), **context)
    body_template = _render(step.get("body_template"), **context)

    lines = [f"Recipient: {prospect.full_name}, {prospect.title or 'unknown title'} at {firm.name}"]
    if signal is not None:
        lines.append(f"Signal hook: {signal.hook_summary}")
        lines.append(
            f'Signal source: {signal.publication or "unknown publication"} '
            f'— "{signal.article_title or ""}"'
        )
        lines.append(f'Evidence you may reference verbatim: "{signal.evidence_quote}"')
    else:
        lines.append("No public signal is attached — do not claim to have seen their writing.")

    if subject_template or body_template:
        lines.append(
            f"Phil's template guidance for this variant — subject: {subject_template!r}, "
            f"body: {body_template!r}. Personalize it, don't just copy it verbatim."
        )
    else:
        lines.append(
            "No fixed template wording has been provided yet for this variant — use good "
            "judgment within the constraints above."
        )
    return "\n".join(lines)


def _dry_run_fixture(prospect: models.Prospect, signal: models.Signal | None) -> DraftOutput:
    hook = signal.hook_summary if signal else "no attached signal"
    return DraftOutput(
        subject=f"Fixture subject for {prospect.first_name or prospect.full_name}",
        body=f"Fixture body referencing: {hook}.",
        linkedin_message=f"Fixture LinkedIn message referencing: {hook}." if signal else None,
    )


def draft_outreach(
    prospect: models.Prospect,
    firm: models.Firm,
    signal: models.Signal | None,
    *,
    llm: AnthropicClient,
    settings: Settings,
    template_variant: dict,
    sender_name: str,
    known_publications: list[str],
    dry_run: bool = False,
) -> DraftResult:
    constraints = settings.raw_message_sequence.get("constraints", {})
    system = SYSTEM_PROMPT_TEMPLATE.format(
        sender_name=sender_name,
        max_words=constraints.get("max_words", 150),
        cta_count=constraints.get("cta_count", 1),
    )
    step = (template_variant.get("steps") or [{}])[0]
    user_content = _build_user_content(prospect, firm, signal, step, sender_name=sender_name)
    fixture = _dry_run_fixture(prospect, signal) if dry_run else None

    output = _call_llm(llm, settings, system, user_content, fixture)
    if output is None:
        return DraftResult(None, None, None, needs_manual=True)

    validation = validate_claims(
        draft_subject=output.subject,
        draft_body=output.body,
        signal=signal,
        known_publications=known_publications,
    )
    if not validation.passed:
        retry_content = (
            user_content
            + "\n\nYour previous draft violated these rules: "
            + "; ".join(v.detail for v in validation.violations)
            + ". Produce a corrected draft that avoids them."
        )
        output = _call_llm(llm, settings, system, retry_content, fixture)
        if output is None:
            return DraftResult(
                None, None, None, needs_manual=True, violations=validation.violations
            )
        validation = validate_claims(
            draft_subject=output.subject,
            draft_body=output.body,
            signal=signal,
            known_publications=known_publications,
        )
        if not validation.passed:
            return DraftResult(
                None, None, None, needs_manual=True, violations=validation.violations
            )

    return DraftResult(
        subject=output.subject, body=output.body, linkedin_message=output.linkedin_message,
        needs_manual=False,
    )


def _call_llm(
    llm: AnthropicClient,
    settings: Settings,
    system: str,
    user_content: str,
    fixture: DraftOutput | None,
) -> DraftOutput | None:
    try:
        return llm.extract_structured(
            model=settings.llm.drafting.model,
            system=system,
            user_content=user_content,
            schema_model=DraftOutput,
            max_tokens=settings.llm.drafting.max_tokens,
            dry_run_fixture=fixture,
        )
    except ExtractionError:
        logger.warning("Drafting call failed", exc_info=True)
        return None
