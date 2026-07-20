"""Lambda entry point: triggered by S3 ObjectCreated on bronze/*.jsonl.gz.

Reads the batch that triggered the event, runs every record through
parse_record (Block 4's pure parser), writes one Parquet file per
(jurisdiction, year) group present in the batch to silver/ (Snappy
compression, Hive-style partition keys), and any rejected records to
rejected/ alongside the original raw record + error message.

This is the only module that combines the pure parser with AWS I/O.
The grouping/key-naming logic below is kept as plain functions
(no boto3 calls inside them) specifically so it stays unit-testable
without mocking S3 — see tests/test_handler.py. Only _read_batch,
_write_silver_group, and _write_rejected actually touch AWS.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import time
import urllib.parse
from collections import defaultdict
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

from src.parser.observability import emit_metrics, log_event
from src.parser.parser import ParsedDoc, RejectedRecord, parse_record

AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
_s3_client = None


def _s3():
    # Lazy singleton: constructing the client at import time makes the
    # module (and anything that imports it, like tests) require AWS
    # credentials just to load. Deferring it means the pure functions
    # below stay importable/testable with zero AWS setup.
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=AWS_REGION)
    return _s3_client


# --- Pure logic (no AWS calls) — unit tested directly -----------------


def doc_id_prefix(doc_id: str, length: int = 12) -> str:
    return doc_id[:length]


def group_by_jurisdiction_year(docs: list[ParsedDoc]) -> dict[tuple[str, str], list[ParsedDoc]]:
    groups: dict[tuple[str, str], list[ParsedDoc]] = defaultdict(list)
    for doc in docs:
        groups[(doc.jurisdiction, doc.year)].append(doc)
    return groups


def silver_key_for_group(jurisdiction: str, year: str, docs: list[ParsedDoc]) -> str:
    """Deterministic given the same set of docs: a single doc_id prefix
    (of the lexicographically-first doc_id in the group) identifies the
    file, no separate UUID/counter needed. Re-running the same batch
    reproduces this exact key, so S3 overwrites rather than duplicates —
    the idempotency property Block 6 verifies.
    """
    file_prefix = doc_id_prefix(sorted(d.doc_id for d in docs)[0])
    return f"silver/jurisdiction={jurisdiction}/year={year}/part-{file_prefix}.parquet"


def rejected_key_for_batch(batch_key: str) -> str:
    """`bronze/ingest_date=2026-07-20/part-0001.jsonl.gz` ->
    `rejected/ingest_date=2026-07-20/part-0001-rejected.jsonl`
    Deterministic per source batch key, same idempotency reasoning as
    silver_key_for_group.
    """
    parts = batch_key.split("/")
    ingest_date_part = next((p for p in parts if p.startswith("ingest_date=")), "ingest_date=unknown")
    batch_name = parts[-1]
    for suffix in (".jsonl.gz", ".jsonl"):
        if batch_name.endswith(suffix):
            batch_name = batch_name[: -len(suffix)]
            break
    return f"rejected/{ingest_date_part}/{batch_name}-rejected.jsonl"


# jurisdiction and year are Hive partition keys — they live in the S3 key
# path (silver/jurisdiction=X/year=Y/...), not in the file itself. Writing
# them into the Parquet payload too would duplicate data already encoded
# in the path and risks a partition/column schema mismatch in Glue/Athena
# depending on engine settings. Block 6's Glue table definition
# deliberately does NOT list these two in its column schema, only in
# partition_keys — this exclusion is what keeps the two consistent.
PARTITION_KEY_FIELDS = {"jurisdiction", "year"}


def parsed_docs_to_parquet_bytes(docs: list[ParsedDoc]) -> bytes:
    rows = [
        {k: v for k, v in d.model_dump(mode="json").items() if k not in PARTITION_KEY_FIELDS} for d in docs
    ]
    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def rejected_records_to_jsonl_bytes(rejected: list[RejectedRecord]) -> bytes:
    lines = [
        json.dumps({"raw": r.raw, "error": r.error, "rejected_at": r.rejected_at.isoformat()})
        for r in rejected
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


# --- AWS I/O ------------------------------------------------------------


def _read_batch(bucket: str, key: str) -> list[dict[str, Any]]:
    body = _s3().get_object(Bucket=bucket, Key=key)["Body"].read()
    if key.endswith(".gz"):
        body = gzip.decompress(body)
    text = body.decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _write_silver_group(bucket: str, jurisdiction: str, year: str, docs: list[ParsedDoc]) -> str:
    key = silver_key_for_group(jurisdiction, year, docs)
    body = parsed_docs_to_parquet_bytes(docs)
    _s3().put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/x-parquet")
    return key


def _write_rejected(bucket: str, batch_key: str, rejected: list[RejectedRecord]) -> str | None:
    if not rejected:
        return None
    key = rejected_key_for_batch(batch_key)
    body = rejected_records_to_jsonl_bytes(rejected)
    _s3().put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    return key


def lambda_handler(event: dict, context: Any) -> dict:
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        started = time.perf_counter()

        log_event(event="batch_started", bucket=bucket, source_key=key)

        raw_records = _read_batch(bucket, key)

        parsed: list[ParsedDoc] = []
        rejected: list[RejectedRecord] = []
        for raw in raw_records:
            result = parse_record(raw)
            if isinstance(result, ParsedDoc):
                parsed.append(result)
            else:
                rejected.append(result)
                # Per-record structured log for every rejection, so the
                # Day 3 failure taxonomy can be built straight from Logs
                # Insights rather than by re-reading rejected/ from S3.
                log_event(
                    event="record_rejected",
                    source_key=key,
                    outcome="rejected",
                    error_type=type(result).__name__,
                    error=result.error.splitlines()[0] if result.error else None,
                    version_id=result.raw.get("version_id"),
                )

        silver_keys = [
            _write_silver_group(bucket, jurisdiction, year, docs)
            for (jurisdiction, year), docs in group_by_jurisdiction_year(parsed).items()
        ]
        rejected_key = _write_rejected(bucket, key, rejected)

        duration_ms = (time.perf_counter() - started) * 1000

        log_event(
            event="batch_completed",
            source_key=key,
            outcome="ok",
            records_in=len(raw_records),
            parsed_count=len(parsed),
            rejected_count=len(rejected),
            silver_files=len(silver_keys),
            duration_ms=round(duration_ms, 2),
        )

        # Separate line, EMF-shaped — this is what CloudWatch turns into
        # real metrics (RecordsParsed / RecordsRejected / ParseDurationMs).
        emit_metrics(
            records_parsed=len(parsed),
            records_rejected=len(rejected),
            parse_duration_ms=duration_ms,
            source_key=key,
        )

        results.append(
            {
                "source_key": key,
                "parsed_count": len(parsed),
                "rejected_count": len(rejected),
                "silver_keys": silver_keys,
                "rejected_key": rejected_key,
            }
        )

    return {"batches_processed": len(results), "results": results}
