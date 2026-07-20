-- Reconciliation as a dbt test, so it runs in CI on every build rather
-- than only when someone remembers to run src/ops/reconcile.py.
--
-- Asserts the fact table hasn't lost or duplicated rows relative to the
-- silver source it's built from. Returns rows only on failure, which is
-- dbt's contract for a singular test.
--
-- Note this is the *warehouse-side* half of reconciliation. The
-- storage-side half (bronze == silver + rejected) can't be expressed in
-- SQL, because rejected/ is raw JSONL with no Glue table over it — that
-- half lives in src/ops/reconcile.py.

with silver_count as (
    select count(*) as n from {{ source('silver', 'silver_judgments') }}
),

fact_count as (
    select count(*) as n from {{ ref('fct_judgment') }}
)

select
    s.n as silver_rows,
    f.n as fact_rows,
    s.n - f.n as difference
from silver_count s
cross join fact_count f
where s.n != f.n
