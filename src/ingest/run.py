"""Block 3 entry point: HF corpus -> stratified sample -> S3 bronze.

Usage:
    python -m src.ingest.run --bucket legal-lakehouse-data-jasminephannd

This is the only module in src/ingest that touches the network or AWS —
sample.py and bronze_writer.py are pure and unit-tested on their own in
tests/test_ingest_sample.py.
"""

from __future__ import annotations

import argparse
import logging

from datasets import load_dataset
from huggingface_hub import dataset_info

from src.ingest.bronze_writer import write_bronze_batches
from src.ingest.config import (
    DATASET_NAME,
    DATASET_SPLIT,
    SHUFFLE_BUFFER_SIZE,
    SHUFFLE_SEED,
    TARGET_SAMPLE_SIZE,
)
from src.ingest.sample import stratified_sample

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def fetch_candidate_pool(pool_size: int = 50_000) -> list[dict]:
    """Streams a shuffled prefix of the corpus into memory.

    `pool_size` needs to stay well above TARGET_SAMPLE_SIZE so
    stratified_sample has enough supply per jurisdiction/year to actually
    stratify against — but this deliberately does NOT materialize the
    full ~230k-row corpus. The dataset's `.shuffle()` uses a reservoir
    buffer (SHUFFLE_BUFFER_SIZE), not a full shuffle, which is the
    standard tradeoff for streaming datasets this large.
    """
    ds = load_dataset(DATASET_NAME, split=DATASET_SPLIT, streaming=True)
    ds = ds.shuffle(seed=SHUFFLE_SEED, buffer_size=SHUFFLE_BUFFER_SIZE)

    pool: list[dict] = []
    for record in ds:
        pool.append(record)
        if len(pool) >= pool_size:
            break
    return pool


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample the corpus into S3 bronze.")
    parser.add_argument(
        "--bucket",
        required=True,
        help="Data lake bucket, e.g. legal-lakehouse-data-jasminephannd",
    )
    parser.add_argument("--target", type=int, default=TARGET_SAMPLE_SIZE)
    parser.add_argument("--pool-size", type=int, default=50_000)
    args = parser.parse_args()

    logger.info(
        "Fetching a shuffled candidate pool of %d records from %s (split=%s)...",
        args.pool_size,
        DATASET_NAME,
        DATASET_SPLIT,
    )
    pool = fetch_candidate_pool(pool_size=args.pool_size)
    logger.info("Pool ready: %d records.", len(pool))

    sample = stratified_sample(pool, target_total=args.target)
    logger.info("Stratified sample: %d records.", len(sample))

    try:
        revision = dataset_info(DATASET_NAME).sha
    except Exception:  # noqa: BLE001 — best-effort only, never block the ingest on this
        logger.warning("Could not resolve dataset revision; recording as unknown.")
        revision = None

    result = write_bronze_batches(sample, bucket=args.bucket, source_revision=revision)
    logger.info(
        "Wrote %d records across %d batch(es), %d bytes, to s3://%s/%s",
        result.record_count,
        len(result.keys),
        result.byte_count,
        args.bucket,
        result.manifest_key.rsplit("/", 1)[0],
    )


if __name__ == "__main__":
    main()
