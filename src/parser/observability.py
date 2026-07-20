"""Structured JSON logging and CloudWatch EMF metrics.

Two reasons this is its own module rather than inline in handler.py:
it's pure (builds dicts, prints them — no AWS SDK calls), so it's
unit-testable; and the EMF format is fiddly enough that getting it wrong
silently produces no metrics at all, which is worth isolating.

**Structured logs.** CloudWatch Logs Insights can query JSON natively
(`fields outcome, duration_ms | filter outcome = "rejected"`). It cannot
query plain strings without regex gymnastics. So every log line is a
single-line JSON object.

**EMF (Embedded Metric Format).** Writing a specially-shaped JSON blob
to stdout makes CloudWatch extract real metrics from it — no PutMetricData
API call, no extra latency, no IAM permission needed. The `_aws` key is
what CloudWatch looks for.
"""

from __future__ import annotations

import json
import time
from typing import Any

METRIC_NAMESPACE = "LegalLakehouse/Parser"


def log_event(**fields: Any) -> None:
    """Emit one structured log line. Keys become queryable fields in
    CloudWatch Logs Insights."""
    print(json.dumps(fields, default=str))


def build_emf_metrics(
    records_parsed: int,
    records_rejected: int,
    parse_duration_ms: float,
    source_key: str,
    timestamp_ms: int | None = None,
) -> dict:
    """Build an EMF payload.

    `Dimensions` is deliberately empty-ish (a single empty set) — adding
    source_key as a dimension would create one CloudWatch metric stream
    per S3 object, which explodes cardinality and cost. It stays a plain
    property instead: visible in logs, not billed as a dimension.
    """
    return {
        "_aws": {
            "Timestamp": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": METRIC_NAMESPACE,
                    "Dimensions": [[]],
                    "Metrics": [
                        {"Name": "RecordsParsed", "Unit": "Count"},
                        {"Name": "RecordsRejected", "Unit": "Count"},
                        {"Name": "ParseDurationMs", "Unit": "Milliseconds"},
                    ],
                }
            ],
        },
        "RecordsParsed": records_parsed,
        "RecordsRejected": records_rejected,
        "ParseDurationMs": round(parse_duration_ms, 2),
        # Properties, not dimensions — queryable in Logs Insights,
        # doesn't multiply metric cardinality.
        "source_key": source_key,
    }


def emit_metrics(
    records_parsed: int,
    records_rejected: int,
    parse_duration_ms: float,
    source_key: str,
) -> None:
    print(json.dumps(build_emf_metrics(records_parsed, records_rejected, parse_duration_ms, source_key)))
