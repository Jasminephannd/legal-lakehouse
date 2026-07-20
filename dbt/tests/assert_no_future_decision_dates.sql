-- Domain-specific singular test: no document can be dated in the future.
--
-- Generic not_null/unique tests check structure; this checks that the
-- data means something sensible. A future-dated judgment is always a
-- source error (bad OCR, a scraper picking up a "next review date", a
-- timezone bug) and it silently poisons any time-series analysis built
-- on dim_date.
--
-- Returns rows only on failure.

select
    doc_id,
    citation,
    decision_date,
    source_url
from {{ ref('fct_judgment') }}
where decision_date is not null
  and decision_date > current_date
