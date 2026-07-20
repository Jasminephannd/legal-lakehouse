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


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-invoke the parser Lambda against an existing bronze key.")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--key", required=True, help="e.g. bronze/ingest_date=2026-07-20/part-0001.jsonl.gz")
    args = parser.parse_args()

    lambda_client = boto3.client("lambda", region_name=AWS_REGION)
    result = invoke_parser(args.bucket, args.key, lambda_client)
    print(json.dumps(result, indent=2))
    print(
        "\nRun this twice against the same --key and compare 'parsed_count' and "
        "'silver_keys' across both runs — identical output on both is the "
        "idempotency proof. Then re-run reconcile.py's Athena count before/after "
        "to confirm the silver row count in Glue didn't grow on the second run."
    )


if __name__ == "__main__":
    main()
