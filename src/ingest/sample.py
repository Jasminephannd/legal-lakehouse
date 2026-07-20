"""Stratified sampling over the corpus.

Pure function, no network/AWS/HF objects — takes a plain sequence of
dicts and returns a subset. run.py is responsible for materializing a
shuffled prefix of the streamed corpus into memory before calling this;
that boundary is what keeps this module unit-testable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from src.ingest.config import KNOWN_JURISDICTIONS, TARGET_SAMPLE_SIZE


def _extract_year(date_value: str | None) -> str:
    """`date` is `YYYY-MM-DD` or null in this corpus. Null becomes the
    same 'unknown' bucket Block 4's parser contract uses for undated
    records, so a missing date doesn't silently get its own unbounded
    quota."""
    if not date_value:
        return "unknown"
    return date_value[:4]


def stratified_sample(
    records: Sequence[dict],
    target_total: int = TARGET_SAMPLE_SIZE,
    jurisdictions: list[str] | None = None,
    year_cap_fraction: float = 0.15,
    quota_overflow_fraction: float = 0.5,
) -> list[dict]:
    """Fill a per-jurisdiction quota, spread across years within each
    jurisdiction, from `records`.

    Two passes:
      1. Each jurisdiction fills up to `target_total / len(jurisdictions)`,
         with no single year contributing more than `year_cap_fraction`
         of that jurisdiction's quota.
      2. If short of `target_total` (a rare jurisdiction ran out of
         supply, or the year cap left quota on the table), jurisdictions
         that already hit their base quota are allowed to take more, up
         to `base_quota * (1 + quota_overflow_fraction)`. This ceiling
         matters because the real corpus is heavily skewed — NSW caselaw
         alone is roughly half of all documents — so an uncapped top-up
         would let one abundant jurisdiction quietly eat the whole
         shortfall and defeat the point of stratifying at all.
    """
    jurisdictions = jurisdictions or KNOWN_JURISDICTIONS
    base_quota = max(1, target_total // len(jurisdictions))
    hard_ceiling = base_quota + max(1, int(base_quota * quota_overflow_fraction))
    year_cap = max(1, int(base_quota * year_cap_fraction))

    counts: dict[str, int] = defaultdict(int)
    year_counts: dict[tuple[str, str], int] = defaultdict(int)
    sample: list[dict] = []
    seen_ids: set[str] = set()

    def try_add(record: dict, quota: int, enforce_year_cap: bool) -> bool:
        jurisdiction = record.get("jurisdiction")
        version_id = record.get("version_id")
        if jurisdiction not in jurisdictions or version_id in seen_ids:
            return False
        if counts[jurisdiction] >= quota:
            return False
        year_key = (jurisdiction, _extract_year(record.get("date")))
        if enforce_year_cap and year_counts[year_key] >= year_cap:
            return False

        sample.append(record)
        counts[jurisdiction] += 1
        year_counts[year_key] += 1
        seen_ids.add(version_id)
        return True

    # Pass 1: base quota, year-capped — this is what actually produces a
    # year spread when there's enough supply to choose from.
    for record in records:
        if len(sample) >= target_total:
            return sample
        try_add(record, base_quota, enforce_year_cap=True)

    # Pass 2: top up toward target_total, bounded by hard_ceiling per
    # jurisdiction, but with the year cap OFF. Keeping it on here would
    # mean a jurisdiction whose supply clusters into one year bucket
    # (undated records are the real example — they all fall into the
    # same "unknown" bucket) can never be topped up past that cap, and
    # the sample would silently end up short of target_total even though
    # supply exists. Pass 1 already did the work of spreading years where
    # possible; pass 2's job is just to hit the target.
    if len(sample) < target_total:
        for record in records:
            if len(sample) >= target_total:
                break
            try_add(record, hard_ceiling, enforce_year_cap=False)

    return sample
