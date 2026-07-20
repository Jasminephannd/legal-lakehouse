"""Inspect silver Parquet from the command line — Parquet is a binary
columnar format, so `aws s3 cp` + a text editor shows you nothing useful.

    # what partitions exist, and how many files/rows in each
    python -m src.ops.peek_silver --bucket <bucket> --summary

    # dump rows from one partition
    python -m src.ops.peek_silver --bucket <bucket> --jurisdiction tasmania --year 2008

    # dump rows from one specific file
    python -m src.ops.peek_silver --bucket <bucket> --key silver/jurisdiction=tasmania/year=2008/part-abc.parquet
"""

from __future__ import annotations

import argparse
import io
import re
from collections import defaultdict

import boto3
import pyarrow.parquet as pq

from src.ingest.config import AWS_REGION

_PARTITION_RE = re.compile(r"silver/jurisdiction=([^/]+)/year=([^/]+)/")


def parse_partition_from_key(key: str) -> tuple[str, str] | None:
    """Hive-style partition values live in the S3 key, not in the file —
    which is exactly why a Parquet file looks like it's 'missing' the
    jurisdiction and year columns when you read it directly."""
    match = _PARTITION_RE.match(key)
    if not match:
        return None
    return match.group(1), match.group(2)


def _list_silver_keys(bucket: str, s3_client, prefix: str = "silver/") -> list[str]:
    keys: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".parquet"):
                keys.append(obj["Key"])
    return sorted(keys)


def _read_table(bucket: str, key: str, s3_client):
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    return pq.read_table(io.BytesIO(body))


def summary(bucket: str, s3_client) -> None:
    keys = _list_silver_keys(bucket, s3_client)
    if not keys:
        print("No parquet files under silver/.")
        return

    per_partition: dict[tuple[str, str], list[str]] = defaultdict(list)
    for key in keys:
        part = parse_partition_from_key(key)
        if part:
            per_partition[part].append(key)

    print(f"{len(keys)} parquet file(s) across {len(per_partition)} partition(s)\n")
    print(f"{'jurisdiction':<22} {'year':<10} {'files':>6} {'rows':>8}")
    print("-" * 50)

    total_rows = 0
    for (jurisdiction, year), part_keys in sorted(per_partition.items()):
        rows = sum(_read_table(bucket, k, s3_client).num_rows for k in part_keys)
        total_rows += rows
        print(f"{jurisdiction:<22} {year:<10} {len(part_keys):>6} {rows:>8}")

    print("-" * 50)
    print(f"{'TOTAL':<22} {'':<10} {len(keys):>6} {total_rows:>8}")


def show_rows(bucket: str, keys: list[str], s3_client, limit: int, truncate: int) -> None:
    for key in keys:
        table = _read_table(bucket, key, s3_client)
        part = parse_partition_from_key(key)

        print(f"\n=== {key}")
        if part:
            # Reunite the partition values with the file's own columns,
            # so what's printed matches what Athena would return.
            print(f"    partition: jurisdiction={part[0]}  year={part[1]}")
        print(f"    {table.num_rows} rows, columns: {', '.join(table.column_names)}\n")

        rows = table.to_pylist()[:limit]
        for i, row in enumerate(rows, 1):
            print(f"  --- row {i} ---")
            for col, value in row.items():
                text = str(value)
                if len(text) > truncate:
                    text = text[:truncate] + f"... [{len(str(value))} chars total]"
                print(f"    {col:<16} {text}")
            print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect silver Parquet files.")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--summary", action="store_true", help="Row/file counts per partition.")
    parser.add_argument("--jurisdiction", help="Filter to one jurisdiction.")
    parser.add_argument("--year", help="Filter to one year (or 'unknown').")
    parser.add_argument("--key", help="Read one specific parquet key.")
    parser.add_argument("--limit", type=int, default=3, help="Rows to print per file.")
    parser.add_argument("--truncate", type=int, default=120, help="Max chars per field.")
    parser.add_argument("--max-files", type=int, default=2, help="Files to read when filtering.")
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=AWS_REGION)

    if args.summary:
        summary(args.bucket, s3)
        return

    if args.key:
        show_rows(args.bucket, [args.key], s3, args.limit, args.truncate)
        return

    prefix = "silver/"
    if args.jurisdiction:
        prefix += f"jurisdiction={args.jurisdiction}/"
        if args.year:
            prefix += f"year={args.year}/"

    keys = _list_silver_keys(args.bucket, s3, prefix=prefix)
    if not keys:
        print(f"No parquet files under {prefix}")
        return

    show_rows(args.bucket, keys[: args.max_files], s3, args.limit, args.truncate)


if __name__ == "__main__":
    main()
