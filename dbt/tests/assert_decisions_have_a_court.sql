{{ config(severity='warn') }}

-- Every document with doc_type = 'decision' should resolve to a real
-- court. Legislation and bills legitimately map to dim_court's
-- 'Not applicable' member; decisions never should.
--
-- Found by querying the unknown member rather than by any test failing:
-- 2 of 764 decisions carry a citation the parser could not resolve to a
-- court abbreviation, so court came back None and they landed in the
-- not-applicable bucket beside 1,236 pieces of legislation.
--
-- This is undercounting by silence. Nothing errors, nothing is dropped,
-- the rows stay queryable — they are simply wrong, and invisible unless
-- someone goes looking. Any per-court aggregate is quietly short by two.
--
-- WHY severity='warn' RATHER THAN error:
-- the honest state of the project is "2 known bad records, cause
-- understood, fix deferred". Setting this to error would fail every
-- build until the citation regex is broadened, which pressures the next
-- person into a hasty parser change to get CI green — the exact
-- incentive that produces over-fitted regexes. A warning keeps the
-- defect visible in every run and its count trending, without holding
-- the pipeline hostage.
--
-- Flip to error once the parser handles these citations, so the class of
-- bug cannot silently return.

select
    f.doc_id,
    f.citation,
    f.doc_type,
    c.court_name
from {{ ref('fct_judgment') }} f
join {{ ref('dim_court') }} c
    on f.court_key = c.court_key
where f.doc_type = 'decision'
  and c.court_level = 'not_applicable'
