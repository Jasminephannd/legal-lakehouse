"""Block 6, step 3: bronze count should equal silver count + rejected
count for a given ingest date. If it doesn't, records are being lost
somewhere between bronze and silver/rejected, and the plan is explicit
that's worth knowing on Day 1, not Day 3.

Usage:
    python -m src.ops.reconcile --bucket legal-lakehouse-data-jasminephannd --ingest-date 2026-07-20

Counting logic (bronze from the manifest, rejected from summing JSONL
line counts, silver via Athena) is pure and unit-tested where it's
plain arithmetic/string parsing (tests/test_reconcile.py). The actual
AWS/Athena calls are not — same split as ingest and parser/handler.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass

import boto3

from src.ingest.config import AWS_REGION

ATHENA_WORKGROUP = "legal-lakehouse"
SILVER_TABLE = "legal_lakehouse.silver_judgments"


@dataclass
class ReconciliationResult:
    bronze_count: int
    silver_count: int
    rejected_count: int

    @property
    def balanced(self) -> bool:
        return self.bronze_count == self.silver_count + self.rejected_count

    @property
    def delta(self) -> int:
        return self.bronze_count - (self.silver_count + self.rejected_count)


def reconcile(bronze_count: int, silver_count: int, rejected_count: int) -> ReconciliationResult:
    return ReconciliationResult(bronze_count, silver_count, rejected_count)


def format_report(result: ReconciliationResult) -> str:
    status = "BALANCED" if result.balanced else "MISMATCH"
    lines = [
        f"bronze:   {result.bronze_count}",
        f"silver:   {result.silver_count}",
        f"rejected: {result.rejected_count}",
        f"silver + rejected = {result.silver_count + result.rejected_count}",
        f"status: {status}",
    ]
    if not result.balanced:
        lines.append(f"delta (bronze - (silver + rejected)): {result.delta}")
        lines.append(
            "Records are being lost somewhere between bronze and silver/rejected — investigate before moving on."
        )
    return "\n".join(lines)


# --- AWS I/O --------------------------------------------------------------


def get_bronze_count(bucket: str, ingest_date: str, s3_client) -> int:
    key = f"bronze/ingest_date={ingest_date}/manifest.json"
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    manifest = json.loads(body)
    return manifest["record_count"]


def get_rejected_count(bucket: str, ingest_date: str, s3_client) -> int:
    prefix = f"rejected/ingest_date={ingest_date}/"
    total = 0
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            body = s3_client.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            total += sum(1 for line in body.decode("utf-8").splitlines() if line.strip())
    return total


def get_silver_count(
    athena_client, poll_interval_seconds: float = 1.0, max_wait_seconds: float = 60.0
) -> int:
    query = f"SELECT COUNT(*) AS n FROM {SILVER_TABLE}"
    exec_id = athena_client.start_query_execution(
        QueryString=query,
        WorkGroup=ATHENA_WORKGROUP,
    )["QueryExecutionId"]

    waited = 0.0
    while waited < max_wait_seconds:
        status = athena_client.get_query_execution(QueryExecutionId=exec_id)["QueryExecution"]["Status"][
            "State"
        ]
        if status in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(poll_interval_seconds)
        waited += poll_interval_seconds

    if status != "SUCCEEDED":
        raise RuntimeError(f"Athena query did not succeed (status={status}): {query}")

    result = athena_client.get_query_results(QueryExecutionId=exec_id)
    # First row is the header ("n"); second row is the actual count.
    return int(result["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile bronze/silver/rejected counts for an ingest date."
    )
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--ingest-date", required=True, help="e.g. 2026-07-20")
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name=AWS_REGION)
    athena = boto3.client("athena", region_name=AWS_REGION)

    bronze_count = get_bronze_count(args.bucket, args.ingest_date, s3)
    rejected_count = get_rejected_count(args.bucket, args.ingest_date, s3)
    silver_count = get_silver_count(athena)

    result = reconcile(bronze_count, silver_count, rejected_count)
    print(format_report(result))


if __name__ == "__main__":
    main()
