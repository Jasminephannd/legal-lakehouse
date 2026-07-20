"""Block 6, step 4: prove idempotency by re-running the parser against
bronze data it's already processed, and confirming the silver row count
doesn't change (same doc_ids -> same S3 keys -> overwrite, not
duplicate).

Rather than re-running the whole ingest (which would need the HF stream
to reproduce byte-identical output — not guaranteed) or re-uploading
bronze objects, this directly re-invokes the deployed Lambda with a
synthetic S3 event pointing at a bronze key that already exists. Compare
`aws lambda invoke`'s reported parsed_count/silver_keys across two runs,
or just re-run the Athena COUNT(*) from reconcile.py before and after.

Usage:
    python -m src.ops.reinvoke_parser --bucket legal-lakehouse-data-jasminephannd --key bronze/ingest_date=2026-07-20/part-0001.jsonl.gz
"""

from __future__ import annotations

import argparse
import json

import boto3

from src.ingest.config import AWS_REGION

LAMBDA_FUNCTION_NAME = "legal-lakehouse-parser"


def build_synthetic_s3_event(bucket: str, key: str) -> dict:
    """Minimal but valid S3 ObjectCreated event shape — only the fields
    handler.lambda_handler actually reads (bucket name, object key) are
    populated with real values; the rest are placeholders satisfying the
    expected structure.
    """
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key},
                },
            }
        ]
    }


def invoke_parser(bucket: str, key: str, lambda_client) -> dict:
    event = build_synthetic_s3_event(bucket, key)
    response = lambda_client.invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode("utf-8"),
    )
    payload = json.loads(response["Payload"].read())
    if response.get("FunctionError"):
        raise RuntimeError(f"Lambda invocation failed: {payload}")
    return payload


def list_bronze_batches(bucket: str, ingest_date: str, s3_client) -> list[str]:
    """All .jsonl.gz batch keys for an ingest date, sorted. Excludes
    manifest.json and any prefix marker objects — same filter the S3
    trigger applies.
    """
    prefix = f"bronze/ingest_date={ingest_date}/"
    keys: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".jsonl.gz"):
                keys.append(obj["Key"])
    return sorted(keys)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Invoke the parser Lambda against existing bronze data (one batch or a whole ingest date)."
    )
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--key", help="A single batch key. Mutually exclusive with --ingest-date.")
    parser.add_argument("--ingest-date", help="Process every batch for this date, e.g. 2026-07-20.")
    args = parser.parse_args()

    if bool(args.key) == bool(args.ingest_date):
        parser.error("Pass exactly one of --key or --ingest-date.")

    lambda_client = boto3.client("lambda", region_name=AWS_REGION)

    if args.key:
        keys = [args.key]
    else:
        s3 = boto3.client("s3", region_name=AWS_REGION)
        keys = list_bronze_batches(args.bucket, args.ingest_date, s3)
        if not keys:
            parser.error(f"No .jsonl.gz batches found for ingest_date={args.ingest_date}")
        print(f"Found {len(keys)} batch(es) to process.\n")

    total_parsed = 0
    total_rejected = 0
    all_silver_keys: set[str] = set()

    for key in keys:
        result = invoke_parser(args.bucket, key, lambda_client)
        for batch in result.get("results", []):
            parsed = batch["parsed_count"]
            rejected = batch["rejected_count"]
            total_parsed += parsed
            total_rejected += rejected
            all_silver_keys.update(batch["silver_keys"])
            print(
                f"{key}  ->  parsed={parsed}  rejected={rejected}  silver_files={len(batch['silver_keys'])}"
            )

    print("\n--- totals ---")
    print(f"parsed:            {total_parsed}")
    print(f"rejected:          {total_rejected}")
    print(f"parsed + rejected: {total_parsed + total_rejected}")
    print(f"distinct silver files written: {len(all_silver_keys)}")
    print(
        "\nIdempotency proof: run this exact command a second time. 'parsed' and "
        "'distinct silver files written' must be identical, because deterministic "
        "doc_ids produce identical S3 keys — S3 overwrites rather than appending. "
        "Confirm with reconcile.py that the Athena row count is unchanged too."
    )


if __name__ == "__main__":
    main()
