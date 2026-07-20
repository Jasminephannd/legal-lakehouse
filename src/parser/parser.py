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
from datetime import date as date_type, datetime, timezone
from typing import Any, Optional, Union

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


def _parse_date(raw_date: Optional[str]) -> Optional[date_type]:
    """The corpus stores `date` as ISO 8601 "YYYY-MM-DD" or null — already
    normalised by the corpus's own scraper, which sidesteps most of the
    "several formats" problem the plan warns about. Still doesn't trust
    it blindly: null and any string that isn't valid ISO-8601 both
    resolve to None here rather than raising.
    """
    if not raw_date:
        return None
    try:
        return datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        return None


def _infer_year(decision_date: Optional[date_type]) -> str:
    # Explicit rule from the plan: a missing/unparseable date becomes the
    # "unknown" partition, not a crash and not a dropped record.
    if decision_date is None:
        return "unknown"
    return str(decision_date.year)


def _infer_court(doc_type: str, citation: str, source: Optional[str]) -> Optional[str]:
    if doc_type != "decision":
        return None
    match = _CITATION_COURT_RE.search(citation or "")
    if match:
        abbrev = match.group(1)
        return COURT_ABBREVIATIONS.get(abbrev, abbrev)
    if source == "high_court_of_australia":
        return "High Court of Australia"
    return None


def parse_record(raw: dict[str, Any]) -> Union[ParsedDoc, RejectedRecord]:
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
