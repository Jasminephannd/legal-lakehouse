"""Manual smoke test: run the real parser against real bronze data,
locally, before any of this goes into a Lambda. Not part of the
pipeline itself — this is a dev tool for "does this actually work
against what's in S3 right now."

Usage:
    python -m src.parser.local_dry_run --bucket legal-lakehouse-data-jasminephannd --ingest-date 2026-07-20

Downloads one bronze batch (by default part-0001.jsonl.gz), runs every
record through parse_record, and prints:
  - how many parsed cleanly vs got rejected, and why
  - a couple of sample ParsedDoc fields so you can eyeball them
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter

import boto3

from src.ingest.config import AWS_REGION
from src.parser.parser import ParsedDoc, RejectedRecord, parse_record


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run the parser against a real bronze batch.")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--ingest-date", required=True, help="e.g. 2026-07-20")
    parser.add_argument("--part", default="part-0001.jsonl.gz")
    parser.add_argument("--show", type=int, default=3, help="How many sample ParsedDocs to print")
    args = parser.parse_args()

    key = f"bronze/ingest_date={args.ingest_date}/{args.part}"
    print(f"Downloading s3://{args.bucket}/{key} ...")

    s3 = boto3.client("s3", region_name=AWS_REGION)
    body = s3.get_object(Bucket=args.bucket, Key=key)["Body"].read()
    lines = gzip.decompress(body).decode("utf-8").splitlines()
    print(f"Batch has {len(lines)} records.\n")

    parsed: list[ParsedDoc] = []
    rejected: list[RejectedRecord] = []
    rejection_reasons: Counter[str] = Counter()

    for line in lines:
        raw = json.loads(line)
        result = parse_record(raw)
        if isinstance(result, ParsedDoc):
            parsed.append(result)
        else:
            rejected.append(result)
            rejection_reasons[result.error] += 1

    print(f"Parsed OK:  {len(parsed)}")
    print(f"Rejected:   {len(rejected)}")

    if rejection_reasons:
        print("\nRejection reasons:")
        for reason, count in rejection_reasons.most_common():
            print(f"  {count:>4}  {reason}")

    if parsed:
        print(f"\nSample of {min(args.show, len(parsed))} parsed doc(s):")
        for doc in parsed[: args.show]:
            print(
                f"  doc_id={doc.doc_id[:12]}...  jurisdiction={doc.jurisdiction}  "
                f"doc_type={doc.doc_type}  court={doc.court}  year={doc.year}  "
                f"text_length={doc.text_length}"
            )

    if rejected:
        print("\nSample of 1 rejected record:")
        r = rejected[0]
        print(f"  error={r.error}")
        print(f"  raw keys={list(r.raw.keys())}")


if __name__ == "__main__":
    main()
