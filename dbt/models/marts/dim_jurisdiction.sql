{{ config(materialized='table') }}

-- Grain: one row per Australian jurisdiction present in the corpus.
--
-- Surrogate keys (not the natural code) on every dimension: the natural
-- key is a source system's identifier, and if the source ever renames or
-- recodes a jurisdiction, every fact row referencing it breaks. A hashed
-- surrogate key insulates the warehouse from that, and keeps the fact
-- table's FK columns a uniform type across all dimensions.

with jurisdictions as (

    select distinct jurisdiction_code
    from {{ ref('stg_judgments') }}
    where jurisdiction_code is not null

),

enriched as (

    select
        {{ dbt_utils.generate_surrogate_key(['jurisdiction_code']) }} as jurisdiction_key,
        jurisdiction_code,

        case jurisdiction_code
            when 'commonwealth'      then 'Commonwealth of Australia'
            when 'new_south_wales'   then 'New South Wales'
            when 'queensland'        then 'Queensland'
            when 'western_australia' then 'Western Australia'
            when 'south_australia'   then 'South Australia'
            when 'tasmania'          then 'Tasmania'
            when 'norfolk_island'    then 'Norfolk Island'
            else initcap(replace(jurisdiction_code, '_', ' '))
        end as jurisdiction_name,

        -- Federal vs state/territory matters for court hierarchy: a
        -- Commonwealth court's decisions bind differently to a state
        -- court's, so this is the split analysts actually filter on.
        case
            when jurisdiction_code in ('commonwealth') then 'federal'
            when jurisdiction_code in ('norfolk_island') then 'territory'
            else 'state'
        end as jurisdiction_level,

        'Australia' as country

    from jurisdictions

)

select * from enriched
