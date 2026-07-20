"""Reprocess a single silver partition from bronze, without touching
anything else.

    python -m src.ops.backfill --jurisdiction new_south_wales --year 2019

Why this is safe to run twice: the parser assigns doc_id = SHA-256 of
source_url, and silver keys are derived from those doc_ids, so
reprocessing the same bronze records writes the same S3 keys — S3
overwrites rather than appends. Running it twice produces identical row
counts, which is the property to demonstrate rather than assert.

Note on scope: bronze batches aren't partitioned by jurisdiction/year
(they're partitioned by ingest_date), so a single (jurisdiction, year)
partition's records are spread across batches. This scans bronze to find
which batches contain matching records, then reprocesses only those —
rather than reprocessing everything and hoping.
"""

from __future__ import annotations

import argparse
import gzip
import json

import boto3

from src.ingest.config import AWS_REGION
from src.ops.reinvoke_parser import invoke_parser


def batches_containing_partition(
    bucket: str,
    jurisdiction: str,
    year: str,
    s3_client,
    ingest_date: str | None = None,
) -> list[str]:
    """Bronze batch keys holding at least one record for this partition.

    Reads and decompresses each batch — fine at this project's scale
    (8 batches x 250 records). At a scale where it isn't, the right fix
    is a bronze-level index or partitioning bronze by jurisdiction too;
    noted rather than pre-built.
    """
    prefix = f"bronze/ingest_date={ingest_date}/" if ingest_date else "bronze/"
    matching: list[str] = []

    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".jsonl.gz"):
                continue

            body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
            text = gzip.decompress(body).decode("utf-8")

            for line in text.splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                rec_year = (rec.get("date") or "")[:4] or "unknown"
                if rec.get("jurisdiction") == jurisdiction and rec_year == year:
                    matching.append(key)
                    break

    return sorted(matching)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reprocess one silver partition from bronze.")
    parser.add_argument("--bucket", default="legal-lakehouse-data-jasminephannd")
    parser.add_argument("--jurisdiction", required=True, help="e.g. new_south_wales")
    parser.add_argument("--year", required=True, help="4-digit year, or 'unknown'")
    parser.add_argument("--ingest-date", help="Restrict the bronze scan to one ingest date.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report which batches would be reprocessed, without invoking the Lambda.",
    )
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=AWS_REGION)

    print(f"Scanning bronze for jurisdiction={args.jurisdiction} year={args.year} ...")
    keys = batches_containing_partition(
        args.bucket, args.jurisdiction, args.year, s3, ingest_date=args.ingest_date
    )

    if not keys:
        print("No bronze batches contain records for that partition. Nothing to do.")
        return

    print(f"{len(keys)} batch(es) contain matching records:")
    for k in keys:
        print(f"  {k}")

    if args.dry_run:
        print("\n--dry-run: stopping before invoking the Lambda.")
        return

    lambda_client = boto3.client("lambda", region_name=AWS_REGION)
    total_parsed = 0
    total_rejected = 0

    print()
    for key in keys:
        result = invoke_parser(args.bucket, key, lambda_client)
        for batch in result.get("results", []):
            total_parsed += batch["parsed_count"]
            total_rejected += batch["rejected_count"]
            print(f"{key} -> parsed={batch['parsed_count']} rejected={batch['rejected_count']}")

    print(f"\nReprocessed {len(keys)} batch(es): parsed={total_parsed} rejected={total_rejected}")
    print(
        "\nNote: batches are reprocessed whole, so records outside the target "
        "partition are rewritten too — to identical keys, so the result is "
        "unchanged. Run this command twice and compare the Athena row count "
        "to confirm."
    )


if __name__ == "__main__":
    main()
