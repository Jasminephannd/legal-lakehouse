-- Guards against the failure mode where the whole suite passes on an
-- empty warehouse.
--
-- This is not hypothetical. One CI run reported PASS=38 with
-- fct_judgment holding 0 rows and dim_court holding 1 (its
-- 'Not applicable' member alone). Every not_null and unique test passed,
-- because a test over zero rows finds zero violations. Even
-- assert_fact_matches_silver_source passed, because the silver query
-- also returned 0 and 0 == 0. The build was green and the warehouse was
-- empty.
--
-- The root cause that time was unregistered Glue partitions, but the
-- category is broader: any upstream break that yields an empty result
-- rather than an error will sail through a suite made entirely of
-- "no row may violate X" assertions. Those tests constrain the rows that
-- exist; none of them requires that rows exist at all.
--
-- So this test asserts the opposite direction: each gold relation must
-- be non-trivially populated. Floors are deliberately low — this is a
-- smoke test for "the pipeline ran", not an assertion about corpus size,
-- which would break every time the sample is resized.
--
-- min_fact_rows is a var so a deliberately small local run can lower it
-- without editing the test:
--   dbt build --vars '{min_fact_rows: 10}'

{% set min_fact_rows = var('min_fact_rows', 100) %}

with counts as (

    select 'fct_judgment'      as relation, count(*) as n, {{ min_fact_rows }} as floor from {{ ref('fct_judgment') }}
    union all
    -- Dimensions only need to prove they were populated at all. dim_court
    -- always has at least its 'Not applicable' member, so a floor of 1
    -- would be satisfied by an empty corpus — hence 2.
    select 'dim_court'         as relation, count(*) as n, 2   as floor from {{ ref('dim_court') }}
    union all
    select 'dim_jurisdiction'  as relation, count(*) as n, 1   as floor from {{ ref('dim_jurisdiction') }}
    union all
    -- dim_date is a generated spine, so its size is known independently
    -- of the corpus. If it is short, the date_spine itself is broken.
    select 'dim_date'          as relation, count(*) as n, 365 as floor from {{ ref('dim_date') }}

)

select
    relation,
    n as actual_rows,
    floor as minimum_expected
from counts
where n < floor
