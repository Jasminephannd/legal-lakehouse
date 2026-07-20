"""Block 3, step 1: inspect the corpus before writing any parsing logic.

Run with:
    python -m src.ingest.inspect_corpus

Confirmed fields (9), from the dataset card and a live check against
huggingface.co on 2026-07-20 — this is the schema Block 4's Pydantic
contract needs to map from:

    version_id     str   unique id for the latest known version
    type           str   primary_legislation | secondary_legislation | bill | decision
    jurisdiction   str   commonwealth | new_south_wales | queensland |
                         western_australia | south_australia | tasmania |
                         norfolk_island
    source         str   e.g. nsw_caselaw, high_court_of_australia, ...
    citation       str   document title, jurisdiction abbreviation appended
    mime           str   MIME type of the original source document
    date           str | None   ISO 8601 "YYYY-MM-DD", or null
    url            str   link to the latest known version
    when_scraped   str   ISO 8601 timestamp, timezone-aware
    text           str   full document text

Two things worth flagging for Block 4 before writing the parser:
  - There is no "court" field. For decisions, `source` (e.g.
    "high_court_of_australia") or a regex over `citation` is the only way
    to recover a court name — it has to be derived, not read directly.
  - There is no "year" field either — derive it from `date`, same as this
    module's config.py already assumes for stratification.
"""
from __future__ import annotations

from datasets import load_dataset

from src.ingest.config import DATASET_NAME, DATASET_SPLIT


def inspect(n: int = 3) -> None:
    ds = load_dataset(DATASET_NAME, split=DATASET_SPLIT, streaming=True)
    it = iter(ds)
    for i in range(n):
        record = next(it)
        print(f"--- record {i} ---")
        for key, value in record.items():
            value_str = str(value)
            print(f"{key!r}: {type(value).__name__} = {value_str[:150]!r}")
        print()


if __name__ == "__main__":
    inspect()
