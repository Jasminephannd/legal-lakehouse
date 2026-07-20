import json

from src.parser.observability import METRIC_NAMESPACE, build_emf_metrics


def test_emf_payload_has_aws_key_cloudwatch_looks_for():
    payload = build_emf_metrics(250, 4, 812.5, "bronze/x/part-0001.jsonl.gz")
    assert "_aws" in payload
    assert payload["_aws"]["CloudWatchMetrics"][0]["Namespace"] == METRIC_NAMESPACE


def test_emf_declares_all_three_metrics():
    payload = build_emf_metrics(250, 4, 812.5, "k")
    names = {m["Name"] for m in payload["_aws"]["CloudWatchMetrics"][0]["Metrics"]}
    assert names == {"RecordsParsed", "RecordsRejected", "ParseDurationMs"}


def test_metric_values_are_present_at_top_level():
    # EMF requires the metric values to be top-level keys, not nested —
    # nesting them silently produces no metrics at all.
    payload = build_emf_metrics(250, 4, 812.5, "k")
    assert payload["RecordsParsed"] == 250
    assert payload["RecordsRejected"] == 4
    assert payload["ParseDurationMs"] == 812.5


def test_source_key_is_a_property_not_a_dimension():
    # Dimensions multiply metric cardinality; one per S3 object would be
    # expensive and useless. It must stay a plain property.
    payload = build_emf_metrics(250, 4, 812.5, "bronze/x/part-0001.jsonl.gz")
    dimensions = payload["_aws"]["CloudWatchMetrics"][0]["Dimensions"]
    assert dimensions == [[]]
    assert payload["source_key"] == "bronze/x/part-0001.jsonl.gz"


def test_payload_is_json_serialisable_single_line():
    payload = build_emf_metrics(1, 0, 1.0, "k")
    serialised = json.dumps(payload)
    assert "\n" not in serialised
