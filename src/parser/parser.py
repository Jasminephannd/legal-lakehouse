"""Bronze record -> ParsedDoc (valid) | RejectedRecord (invalid).

Pure function — no AWS, no S3, no I/O beyond what's already in the input
dict. This is what Block 5's Lambda handler calls per record; testing it
(tests/test_parser.py) requires no AWS access and runs in milliseconds.
Writing RejectedRecord instances out to the rejected/ prefix is the
handler's job, not this module's — keeping S3 out of here is what makes
it fast to iterate on.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from src.parser.models import ParsedDoc

# Neutral citation format used throughout: "[YEAR] ABBREV NUMBER", e.g.
# "[2013] NSWSC 1668" or "[1956] HCA 14".
_CITATION_COURT_RE = re.compile(r"\[\d{4}\]\s+([A-Za-z]+)\b")

# Deliberately not exhaustive. The corpus's `source` field maps 1:1 to a
# single court only for High Court decisions — nsw_caselaw and
# federal_court_of_australia each span many courts/tribunals (Supreme
# Court, District Court, Children's Court, various tribunals...), so
# court has to come from the citation's neutral abbreviation instead.
# This table covers what shows up most often; anything unmapped falls
# back to the raw abbreviation rather than being silently dropped — see
# _infer_court.
COURT_ABBREVIATIONS = {
    "HCA": "High Court of Australia",
    "FCA": "Federal Court of Australia",
    "FCAFC": "Full Court of the Federal Court of Australia",
    "FamCA": "Family Court of Australia",
    "FamCAFC": "Full Court of the Family Court of Australia",
    "NSWSC": "Supreme Court of New South Wales",
    "NSWCA": "Court of Appeal (NSW)",
    "NSWCCA": "Court of Criminal Appeal (NSW)",
    "NSWDC": "District Court of New South Wales",
    "NSWLC": "Local Court of New South Wales",
    "NSWChC": "Children's Court of New South Wales",
    "NSWCATAP": "NSW Civil and Administrative Tribunal, Appeal Panel",
    "NSWCATCD": "NSW Civil and Administrative Tribunal, Consumer and Commercial Division",
    "NSWCAT": "NSW Civil and Administrative Tribunal",
    "NSWIRComm": "Industrial Relations Commission of New South Wales",
    "NSWLEC": "Land and Environment Court of New South Wales",
    "QSC": "Supreme Court of Queensland",
    "QCA": "Court of Appeal (Qld)",
    "QDC": "District Court of Queensland",
    "QCAT": "Queensland Civil and Administrative Tribunal",
    "WASC": "Supreme Court of Western Australia",
    "WASCA": "Court of Appeal (WA)",
    "WADC": "District Court of Western Australia",
    "SASC": "Supreme Court of South Australia",
    "SASCFC": "Full Court of the Supreme Court of South Australia",
    "SADC": "District Court of South Australia",
    "TASSC": "Supreme Court of Tasmania",
    "TASFC": "Full Court of the Supreme Court of Tasmania",
}


@dataclass
class RejectedRecord:
    raw: dict[str, Any]
    error: str
    rejected_at: datetime


def _sha256_doc_id(source_url: str) -> str:
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()


# Ordered most- to least-specific. The corpus's dataset card documents
# `date` as "YYYY-MM-DD", but the actual values are 19 characters —
# "2008-10-08 00:00:00" — i.e. a datetime with a zeroed time component.
# An earlier version of this function only accepted "%Y-%m-%d" and
# therefore silently failed on EVERY dated record, dumping the whole
# corpus into the year='unknown' partition.
#
# The lesson, which the project plan states explicitly and I under-applied:
# don't trust a handed-over schema. Verifying the field *names* against
# the dataset card wasn't enough — the value *format* needed checking
# against real values too.
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",  # the format the corpus actually uses
    "%Y-%m-%dT%H:%M:%S",  # ISO-8601 with a T separator
    "%Y-%m-%d",  # the format the dataset card documents
)


def _parse_date(raw_date: str | None) -> date_type | None:
    """Parse the corpus's `date` field to a date, or None.

    Tolerant by design: null, empty, and any unrecognised format all
    resolve to None rather than raising, so a single odd value can never
    kill a batch. Callers map None onto the year='unknown' partition.
    """
    if not raw_date:
        return None

    value = raw_date.strip()
    if not value:
        return None

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    # Last resort: fromisoformat handles offsets and fractional seconds
    # that the explicit formats above don't cover.
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _infer_year(decision_date: date_type | None) -> str:
    # Explicit rule from the plan: a missing/unparseable date becomes the
    # "unknown" partition, not a crash and not a dropped record.
    if decision_date is None:
        return "unknown"
    return str(decision_date.year)


def _infer_court(doc_type: str, citation: str, source: str | None) -> str | None:
    if doc_type != "decision":
        return None
    match = _CITATION_COURT_RE.search(citation or "")
    if match:
        abbrev = match.group(1)
        return COURT_ABBREVIATIONS.get(abbrev, abbrev)
    if source == "high_court_of_australia":
        return "High Court of Australia"
    return None


def parse_record(raw: dict[str, Any]) -> ParsedDoc | RejectedRecord:
    now = datetime.now(timezone.utc)
    try:
        source_url = raw.get("url") or ""
        citation = raw.get("citation") or ""
        doc_type = raw.get("type") or ""
        decision_date = _parse_date(raw.get("date"))

        doc = ParsedDoc(
            doc_id=_sha256_doc_id(source_url) if source_url else "",
            jurisdiction=raw.get("jurisdiction") or "",
            doc_type=doc_type,
            court=_infer_court(doc_type, citation, raw.get("source")),
            citation=citation,
            decision_date=decision_date,
            year=_infer_year(decision_date),
            source_url=source_url,
            text=raw.get("text") or "",
            text_length=len(raw.get("text") or ""),
            ingested_at=now,
        )
        return doc
    except (ValidationError, ValueError) as exc:
        return RejectedRecord(raw=raw, error=str(exc), rejected_at=now)
