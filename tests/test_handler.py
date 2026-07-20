import io
from datetime import datetime, timezone

import pyarrow.parquet as pq

from src.parser.handler import (
    group_by_jurisdiction_year,
    parsed_docs_to_parquet_bytes,
    rejected_key_for_batch,
    silver_key_for_group,
)
from src.parser.parser import ParsedDoc


def _doc(doc_id: str, jurisdiction: str = "new_south_wales", year: str = "2020") -> ParsedDoc:
    return ParsedDoc(
        doc_id=doc_id,
        jurisdiction=jurisdiction,
        doc_type="decision",
        court=None,
        citation="Test v Case [2020] NSWSC 1",
        decision_date=None,
        year=year,
        source_url=f"https://example.com/{doc_id}",
        text="some text",
        text_length=9,
        ingested_at=datetime.now(timezone.utc),
    )


def test_group_by_jurisdiction_year_splits_correctly():
    docs = [
        _doc("aaa", jurisdiction="new_south_wales", year="2019"),
        _doc("bbb", jurisdiction="new_south_wales", year="2020"),
        _doc("ccc", jurisdiction="tasmania", year="2020"),
    ]
    groups = group_by_jurisdiction_year(docs)
    assert set(groups.keys()) == {
        ("new_south_wales", "2019"),
        ("new_south_wales", "2020"),
        ("tasmania", "2020"),
    }
    assert len(groups[("new_south_wales", "2020")]) == 1


def test_silver_key_is_deterministic_for_same_group():
    docs = [_doc("bbbbbbbbbbbbbbbb"), _doc("aaaaaaaaaaaaaaaa")]
    key1 = silver_key_for_group("new_south_wales", "2020", docs)
    key2 = silver_key_for_group("new_south_wales", "2020", list(reversed(docs)))
    assert key1 == key2  # order-independent — sorted internally


def test_silver_key_uses_hive_style_partitioning():
    docs = [_doc("abc123")]
    key = silver_key_for_group("tasmania", "2019", docs)
    assert key == "silver/jurisdiction=tasmania/year=2019/part-abc123.parquet"


def test_rejected_key_derives_from_gz_batch_key():
    key = rejected_key_for_batch("bronze/ingest_date=2026-07-20/part-0001.jsonl.gz")
    assert key == "rejected/ingest_date=2026-07-20/part-0001-rejected.jsonl"


def test_rejected_key_handles_plain_jsonl_batch_key():
    key = rejected_key_for_batch("bronze/ingest_date=2026-07-20/part-0002.jsonl")
    assert key == "rejected/ingest_date=2026-07-20/part-0002-rejected.jsonl"


def test_rejected_key_falls_back_when_no_ingest_date_segment():
    key = rejected_key_for_batch("bronze/part-weird.jsonl.gz")
    assert key == "rejected/ingest_date=unknown/part-weird-rejected.jsonl"


def test_parquet_bytes_exclude_partition_key_fields():
    # jurisdiction and year are Hive partition keys — they belong in the
    # S3 key path, not duplicated inside the Parquet file. This is the
    # bug caught before any real data flowed through the Lambda: the
    # Glue table (Block 6) only declares these two as partition_keys, not
    # as columns, so writing them into the file too would mismatch.
    docs = [_doc("abc123", jurisdiction="tasmania", year="2019")]
    body = parsed_docs_to_parquet_bytes(docs)
    table = pq.read_table(io.BytesIO(body))
    assert "jurisdiction" not in table.column_names
    assert "year" not in table.column_names
    assert "doc_id" in table.column_names


def test_parquet_bytes_preserve_row_count_and_doc_id():
    docs = [_doc("aaa"), _doc("bbb"), _doc("ccc")]
    body = parsed_docs_to_parquet_bytes(docs)
    table = pq.read_table(io.BytesIO(body))
    assert table.num_rows == 3
    assert set(table.column("doc_id").to_pylist()) == {"aaa", "bbb", "ccc"}
