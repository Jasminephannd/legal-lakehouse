"""The silver contract. Defined before the parser, per the plan — this
is what every record in silver must satisfy, regardless of how it got
there.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from pydantic import BaseModel, field_validator

# The four doc_type values the raw corpus actually uses (confirmed
# against the dataset card in Block 3 — see src/ingest/inspect_corpus.py).
DOC_TYPES = {"primary_legislation", "secondary_legislation", "bill", "decision"}


class ParsedDoc(BaseModel):
    doc_id: str  # SHA-256 hex digest of source_url — deterministic, gives idempotency
    jurisdiction: str
    doc_type: str
    court: str | None = None  # only populated for doc_type == "decision"; see parser.py
    citation: str
    decision_date: date_type | None = None
    year: str  # 4-digit string, or "unknown" — see parser.py's _infer_year
    source_url: str
    text: str
    text_length: int
    ingested_at: datetime

    @field_validator("text")
    @classmethod
    def text_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text is empty")
        return v

    @field_validator("jurisdiction")
    @classmethod
    def jurisdiction_must_be_present(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("jurisdiction is missing")
        return v

    @field_validator("source_url")
    @classmethod
    def source_url_must_be_present(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source_url is missing")
        return v

    @field_validator("doc_type")
    @classmethod
    def doc_type_must_be_known(cls, v: str) -> str:
        if v not in DOC_TYPES:
            raise ValueError(f"unknown doc_type: {v!r}")
        return v
