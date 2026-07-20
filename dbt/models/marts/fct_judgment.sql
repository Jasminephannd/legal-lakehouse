{{
    config(
        materialized='incremental',
        unique_key='doc_id',
        incremental_strategy='merge',
        partitioned_by=['jurisdiction_code', 'year_partition']
    )
}}

-- GRAIN: one row per legal document (one row per doc_id).
--
-- Incremental with unique_key=doc_id, not full refresh: doc_id is the
-- deterministic SHA-256 the parser assigns, so re-processing a batch
-- merges over the existing row instead of duplicating it. That makes the
-- warehouse layer idempotent in the same way the S3 layer already is.
--
-- Full refresh would also work at 2,000 rows, but it wouldn't survive
-- growth, and the incremental filter below is the piece that has to be
-- right before scale makes it matter.

with staged as (

    select * from {{ ref('stg_judgments') }}

    {% if is_incremental() %}
    -- Only reprocess rows the parser touched since the last build.
    where ingested_at > (select coalesce(max(ingested_at), from_unixtime(0)) from {{ this }})
    {% endif %}

),

final as (

    select
        -- Degenerate dimension: the document's own identity, no separate
        -- dim table needed.
        s.doc_id,
        s.citation,
        s.source_url,

        -- Foreign keys to the dimensions. coalesce onto the explicit
        -- unknown/not-applicable members so no FK is ever null — that's
        -- what lets the relationships tests below be strict.
        {{ dbt_utils.generate_surrogate_key(['s.jurisdiction_code']) }} as jurisdiction_key,

        coalesce(
            {{ dbt_utils.generate_surrogate_key(['s.court_name']) }},
            {{ dbt_utils.generate_surrogate_key(["'__not_applicable__'"]) }}
        ) as court_key,

        coalesce(
            {{ dbt_utils.generate_surrogate_key(['s.decision_date']) }},
            {{ dbt_utils.generate_surrogate_key(["'__unknown__'"]) }}
        ) as date_key,

        s.doc_type,
        s.decision_date,
        s.decision_year,

        -- Measures.
        s.text_length,
        s.word_count,

        -- Partition keys, kept last per Athena's requirement that
        -- partition columns are the trailing columns of the select.
        s.ingested_at,
        s.jurisdiction_code,
        s.year_partition

    from staged s

)

select * from final
