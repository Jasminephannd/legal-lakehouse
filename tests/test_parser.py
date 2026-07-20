import hashlib

from src.parser.parser import RejectedRecord, parse_record
from src.parser.models import ParsedDoc


def _raw(**overrides) -> dict:
    base = {
        "version_id": "nsw_caselaw:abc123",
        "type": "decision",
        "jurisdiction": "new_south_wales",
        "source": "nsw_caselaw",
        "citation": "Insurance Australia Limited t/as NRMA Insurance v Banos (No 2) [2013] NSWSC 1668",
        "mime": "text/html",
        "date": "2013-11-13",
        "url": "https://www.caselaw.nsw.gov.au/decision/54a63c143004de94513db49f",
        "when_scraped": "2024-09-13T22:44:32+10:00",
        "text": "Supreme Court New South Wales. Judgment text follows.",
    }
    base.update(overrides)
    return base


def test_happy_path_produces_valid_parsed_doc():
    result = parse_record(_raw())
    assert isinstance(result, ParsedDoc)
    assert result.jurisdiction == "new_south_wales"
    assert result.doc_type == "decision"
    assert result.year == "2013"
    assert result.text_length == len(_raw()["text"])


def test_doc_id_is_deterministic_sha256_of_source_url():
    url = _raw()["url"]
    result = parse_record(_raw())
    assert isinstance(result, ParsedDoc)
    assert result.doc_id == hashlib.sha256(url.encode("utf-8")).hexdigest()


def test_missing_date_defaults_to_unknown_year_not_rejected():
    result = parse_record(_raw(date=None))
    assert isinstance(result, ParsedDoc)
    assert result.decision_date is None
    assert result.year == "unknown"


def test_malformed_date_defaults_to_unknown_year_not_a_crash():
    result = parse_record(_raw(date="13th of November, 2013"))
    assert isinstance(result, ParsedDoc)
    assert result.decision_date is None
    assert result.year == "unknown"


def test_empty_text_is_rejected():
    result = parse_record(_raw(text="   "))
    assert isinstance(result, RejectedRecord)
    assert "text" in result.error


def test_missing_jurisdiction_is_rejected():
    result = parse_record(_raw(jurisdiction=None))
    assert isinstance(result, RejectedRecord)
    assert "jurisdiction" in result.error


def test_missing_source_url_is_rejected():
    result = parse_record(_raw(url=None))
    assert isinstance(result, RejectedRecord)
    assert "source_url" in result.error


def test_unknown_doc_type_is_rejected():
    result = parse_record(_raw(type="press_release"))
    assert isinstance(result, RejectedRecord)
    assert "doc_type" in result.error


def test_duplicate_detection_same_url_yields_same_doc_id():
    # This is the idempotency property the plan calls out explicitly:
    # re-running the pipeline over the same source data must produce
    # identical doc_ids so silver overwrites rather than duplicates.
    first = parse_record(_raw())
    second = parse_record(_raw(citation="A slightly different citation string"))
    assert isinstance(first, ParsedDoc) and isinstance(second, ParsedDoc)
    assert first.doc_id == second.doc_id


def test_court_inferred_from_citation_for_decisions():
    result = parse_record(_raw())
    assert isinstance(result, ParsedDoc)
    assert result.court == "Supreme Court of New South Wales"


def test_court_is_none_for_non_decision_doc_types():
    result = parse_record(
        _raw(
            type="primary_legislation",
            citation="Government Advertising Act 2011 (NSW)",
            source="nsw_legislation",
        )
    )
    assert isinstance(result, ParsedDoc)
    assert result.court is None
