"""Writes sampled records to S3 bronze as gzipped JSONL batches, plus a
manifest. Bronze is immutable and untransformed — this module batches
and compresses only; it does not clean, validate, or reshape records.
That's Block 4's job, working from silver.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

import boto3

from src.ingest.config import AWS_REGION, BATCH_SIZE, DATASET_NAME


def batch_records(records: Sequence[dict], batch_size: int = BATCH_SIZE) -> Iterator[list[dict]]:
    for i in range(0, len(records), batch_size):
        yield list(records[i : i + batch_size])


def records_to_gzip_jsonl(records: Sequence[dict]) -> bytes:
    lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    return gzip.compress((lines + "\n").encode("utf-8"))


@dataclass
class BronzeWriteResult:
    keys: list[str]
    manifest_key: str
    record_count: int
    byte_count: int


def write_bronze_batches(
    records: Sequence[dict],
    bucket: str,
    ingest_date: date | None = None,
    batch_size: int = BATCH_SIZE,
    source_revision: str | None = None,
    s3_client=None,
) -> BronzeWriteResult:
    """Writes `records` to
    `bronze/ingest_date=YYYY-MM-DD/part-NNNN.jsonl.gz` and a
    `manifest.json` alongside them. The manifest (record count, byte
    count, source revision) is what Block 6's reconciliation checks
    bronze count against.
    """
    ingest_date = ingest_date or datetime.now(timezone.utc).date()
    s3 = s3_client or boto3.client("s3", region_name=AWS_REGION)
    prefix = f"bronze/ingest_date={ingest_date.isoformat()}/"

    keys: list[str] = []
    total_bytes = 0

    for i, batch in enumerate(batch_records(records, batch_size), start=1):
        body = records_to_gzip_jsonl(batch)
        key = f"{prefix}part-{i:04d}.jsonl.gz"
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/gzip",
            ContentEncoding="gzip",
        )
        keys.append(key)
        total_bytes += len(body)

    manifest = {
        "ingest_date": ingest_date.isoformat(),
        "record_count": len(records),
        "byte_count": total_bytes,
        "batch_count": len(keys),
        "batch_keys": keys,
        "source_dataset": DATASET_NAME,
        "source_revision": source_revision,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_key = f"{prefix}manifest.json"
    s3.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=json.dumps(manifest, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    return BronzeWriteResult(
        keys=keys,
        manifest_key=manifest_key,
        record_count=len(records),
        byte_count=total_bytes,
    )
