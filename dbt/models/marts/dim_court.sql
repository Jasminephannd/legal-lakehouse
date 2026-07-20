{{ config(materialized='table') }}

-- Grain: one row per distinct court name appearing on a decision.
--
-- Only decisions have courts — legislation and bills don't. Rather than
-- leaving those facts with a null FK (which breaks relationships tests
-- and forces outer joins downstream), an explicit 'Not applicable'
-- member is added below and non-decision facts point at it. That's the
-- standard dimensional treatment for "this attribute doesn't apply."

with courts as (

    select distinct
        court_name,
        jurisdiction_code
    from {{ ref('stg_judgments') }}
    where court_name is not null

),

enriched as (

    select
        {{ dbt_utils.generate_surrogate_key(['court_name']) }} as court_key,
        court_name,
        {{ dbt_utils.generate_surrogate_key(['jurisdiction_code']) }} as jurisdiction_key,
        jurisdiction_code,

        -- Inferred from name patterns, per the plan. Appellate courts
        -- hear appeals from first-instance courts; the distinction is
        -- the most common analytical cut in case law.
        case
            when lower(court_name) like '%court of appeal%'          then 'appellate'
            when lower(court_name) like '%court of criminal appeal%' then 'appellate'
            when lower(court_name) like '%full court%'               then 'appellate'
            when lower(court_name) like '%high court%'               then 'appellate'
            when lower(court_name) like '%tribunal%'                 then 'tribunal'
            else 'first_instance'
        end as court_level,

        case
            when lower(court_name) like '%tribunal%' then false
            else true
        end as is_court

    from courts

),

-- Explicit unknown member so non-decision facts have a real FK.
with_unknown as (

    select * from enriched

    union all

    select
        {{ dbt_utils.generate_surrogate_key(["'__not_applicable__'"]) }} as court_key,
        'Not applicable'   as court_name,
        cast(null as varchar) as jurisdiction_key,
        cast(null as varchar) as jurisdiction_code,
        'not_applicable'   as court_level,
        false              as is_court

)

select * from with_unknown
