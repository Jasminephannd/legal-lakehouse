"""Shared constants for the bronze ingest step.

Dataset identifiers below were verified directly against the dataset's
page on huggingface.co on 2026-07-20 — see the two flags inline. Neither
was guessed; both were confirmed by actually reading the dataset card
rather than trusting the plan's snippet or any cached assumption.
"""
from __future__ import annotations

# FLAG: the plan (and most older tutorials) reference
# "umarbutler/open-australian-legal-corpus". That path now 302-redirects —
# the dataset is hosted under the "isaacus" org. Using the canonical id
# directly instead of relying on the redirect.
DATASET_NAME = "isaacus/open-australian-legal-corpus"

# FLAG: the plan's inspection snippet uses split="train". This dataset
# only defines a "corpus" split — confirmed via the dataset's own usage
# example on its HF page ("load_dataset(..., split='corpus')"). "train"
# does not exist here and load_dataset() would fail with that value.
DATASET_SPLIT = "corpus"

TARGET_SAMPLE_SIZE = 2_000
BATCH_SIZE = 250

# Reservoir/shuffle buffer for the streaming dataset. The corpus is
# ~230k rows stored in source-grouped order (the raw stream starts with
# a long unbroken run of Tasmanian legislation, then NSW, etc. — see
# inspect_corpus.py output). Shuffling within a buffer avoids sampling
# straight off that ordering.
SHUFFLE_BUFFER_SIZE = 20_000
SHUFFLE_SEED = 42

AWS_REGION = "ap-southeast-2"

# Confirmed jurisdiction values from the dataset card's schema table.
KNOWN_JURISDICTIONS = [
    "commonwealth",
    "new_south_wales",
    "queensland",
    "western_australia",
    "south_australia",
    "tasmania",
    "norfolk_island",
]
