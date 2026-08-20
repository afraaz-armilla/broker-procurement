"""Structured-extraction schema for Path A (§4). One record per article/post."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignalExtraction(BaseModel):
    author_name: str | None = Field(
        None, description="Name of the broker/insurance professional bylined or quoted, if any"
    )
    author_title: str | None = None
    firm_name: str | None = None
    firm_domain_guess: str | None = None
    article_url: str
    article_title: str | None = None
    published_date: str | None = None
    ai_insurance_relevance: float = Field(
        ge=0.0, le=1.0, description="0-1: how directly this concerns AI insurance/AI liability"
    )
    hook_summary: str = ""
    evidence_quote: str = Field(
        "", description="Verbatim quote from the supplied text — never invented"
    )
