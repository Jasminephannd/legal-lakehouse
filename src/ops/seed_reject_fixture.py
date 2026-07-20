"""Writes a small, deliberately-malformed bronze batch so the rejected/
path is exercised against real infrastructure.

Why this exists: the HF corpus is already curated, so all 2,000 real
records pass validation and rejected/ stays empty. An error path that has
never actually run is not a proven error path — this seeds one on purpose
rather than pretending the source data is dirty.

It writes under its own ingest_date (`<date>-reject-fixture`) so it never
contaminates the real ingest's counts or reconciliation. Each record here
targets exactly one validator in src/parser/models.py.

Usage:
    python -m src.ops.seed_reject_fixture --bucket legal-lakehouse-data-jasminephannd
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

import boto3

from src.ingest.config import AWS_REGION


def _base_record(version_id: str) -> dict:
    return {
        "version_id": version_id,
        "type": "decision",
        "jurisdiction": "new_south_wales",
        "source": "nsw_caselaw",
        "citation": "Fixture v Fixture [2020] NSWSC 1",
        "mime": "text/html",
        "date": "2020-01-01",
        "url": f"https://example.invalid/fixture/{version_id}",
        "when_scraped": "2026-07-20T00:00:00+10:00",
        "text": "Fixture judgment text.",
    }


def build_fixture_records() -> list[dict]:
    """One record per validator, plus one valid control.

    The control matters: it proves a bad record doesn't take the whole
    batch down with it — the plan's "never let one bad record kill the
    batch" requirement.
    """
    records = []

    # 1. Valid control — must still land in silver.
    records.append(_base_record("fixture-valid-control"))

    # 2. Empty text -> ParsedDoc.text_must_not_be_empty
    r = _base_record("fixture-empty-text")
    r["text"] = "   "
    records.append(r)

    # 3. Missing jurisdiction -> jurisdiction_must_be_present
    r = _base_record("fixture-no-jurisdiction")
    r["jurisdiction"] = None
    records.append(r)

    # 4. Missing url -> source_url_must_be_present
    r = _base_record("fixture-no-url")
    r["url"] = None
    records.append(r)

    # 5. Unrecognised doc_type -> doc_type_must_be_known
    r = _base_record("fixture-bad-doctype")
    r["type"] = "press_release"
    records.append(r)

    # 6. Malformed date — NOT a rejection. Should parse fine and land in
    #    the year="unknown" partition. Included to prove the documented
    #    behaviour holds against real infra, not just in unit tests.
    r = _base_record("fixture-bad-date")
    r["date"] = "the 3rd of March, 2019"
    records.append(r)

    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a deliberately-malformed bronze batch.")
    parser.add_argument("--bucket", required=True)
    args = parser.parse_args()

    records = build_fixture_records()
    fixture_date_label = f"{date.today().isoformat()}-reject-fixture"

    s3 = boto3.client("s3", region_name=AWS_REGION)
    prefix = f"bronze/ingest_date={fixture_date_label}/"

    import json

    from src.ingest.bronze_writer import records_to_gzip_jsonl

    body = records_to_gzip_jsonl(records)
    key = f"{prefix}part-0001.jsonl.gz"
    s3.put_object(
        Bucket=args.bucket,
        Key=key,
        Body=body,
        ContentType="application/gzip",
        ContentEncoding="gzip",
    )

    manifest = {
        "ingest_date": fixture_date_label,
        "record_count": len(records),
        "byte_count": len(body),
        "batch_count": 1,
        "batch_keys": [key],
        "source_dataset": "SYNTHETIC-reject-path-fixture",
        "source_revision": None,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "note": "Deliberately malformed records to exercise the rejected/ path. Not real corpus data.",
    }
    s3.put_object(
        Bucket=args.bucket,
        Key=f"{prefix}manifest.json",
        Body=json.dumps(manifest, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    print(f"Wrote {len(records)} fixture records to s3://{args.bucket}/{key}")
    print("  expected: 2 parsed (valid control + bad-date), 4 rejected\n")
    print("Now run the parser over it:")
    print(f"  python -m src.ops.reinvoke_parser --bucket {args.bucket} --ingest-date {fixture_date_label}")
    print("\nThen reconcile it (bronze 6 = silver 2 + rejected 4):")
    print(f"  python -m src.ops.reconcile --bucket {args.bucket} --ingest-date {fixture_date_label}")


if __name__ == "__main__":
    main()
