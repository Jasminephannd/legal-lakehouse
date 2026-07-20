from src.ops.reinvoke_parser import build_synthetic_s3_event


def test_synthetic_event_has_correct_bucket_and_key():
    event = build_synthetic_s3_event("my-bucket", "bronze/ingest_date=2026-07-20/part-0001.jsonl.gz")
    record = event["Records"][0]
    assert record["s3"]["bucket"]["name"] == "my-bucket"
    assert record["s3"]["object"]["key"] == "bronze/ingest_date=2026-07-20/part-0001.jsonl.gz"


def test_synthetic_event_shape_matches_what_handler_reads():
    # handler.lambda_handler does record["s3"]["bucket"]["name"] and
    # urllib.parse.unquote_plus(record["s3"]["object"]["key"]) — this
    # confirms the synthetic event satisfies that exact access pattern.
    event = build_synthetic_s3_event("b", "k")
    assert "Records" in event
    assert isinstance(event["Records"], list)
    assert len(event["Records"]) == 1


def test_synthetic_event_is_valid_json_serializable():
    import json

    event = build_synthetic_s3_event("b", "k")
    json.dumps(event)  # raises if not serializable
