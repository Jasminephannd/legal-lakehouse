{{ config(materialized='table') }}

-- Grain: one row per calendar day across the corpus's date range.
--
-- Generated with dbt_utils.date_spine rather than hand-written SQL.
-- Bounds are wide and static on purpose: the corpus contains 19th- and
-- early-20th-century legislation still in force, and a spine that had to
-- be recomputed from min(decision_date) every run would change width as
-- data arrives.
--
-- Like dim_court, this carries an explicit unknown member — roughly a
-- fifth of the corpus has no parseable date (the 'unknown' partition),
-- and those facts still need a valid FK.

{% set date_start = "1900-01-01" %}
{% set date_end = "2031-01-01" %}

with spine as (

    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('" ~ date_start ~ "' as date)",
        end_date="cast('" ~ date_end ~ "' as date)"
    ) }}

),

enriched as (

    select
        {{ dbt_utils.generate_surrogate_key(['date_day']) }} as date_key,
        cast(date_day as date)            as full_date,
        year(date_day)                    as year,
        quarter(date_day)                 as quarter,
        month(date_day)                   as month,
        format_datetime(date_day, 'MMMM') as month_name,
        day(date_day)                     as day_of_month,
        day_of_week(date_day)             as day_of_week,
        format_datetime(date_day, 'EEEE') as day_name,
        cast(floor(year(date_day) / 10) * 10 as integer) as decade,
        day_of_week(date_day) in (6, 7)   as is_weekend

    from spine

),

with_unknown as (

    select * from enriched

    union all

    select
        {{ dbt_utils.generate_surrogate_key(["'__unknown__'"]) }} as date_key,
        cast(null as date)    as full_date,
        cast(null as bigint)  as year,
        cast(null as bigint)  as quarter,
        cast(null as bigint)  as month,
        cast(null as varchar) as month_name,
        cast(null as bigint)  as day_of_month,
        cast(null as bigint)  as day_of_week,
        cast(null as varchar) as day_name,
        cast(null as integer) as decade,
        false                 as is_weekend

)

select * from with_unknown
