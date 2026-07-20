{{
    config(
        materialized='incremental',
        unique_key='doc_id',
        incremental_strategy='merge',
        table_type='iceberg'
    )
}}

-- table_type='iceberg' is required, not decorative: dbt-athena only
-- supports the `merge` incremental strategy on Iceberg tables. Hive
-- tables are limited to `insert_overwrite` or `append`, neither of which
-- can upsert on a unique_key — append would duplicate on every re-run,
-- defeating the whole point.
--
-- Not partitioned. Athena/Iceberg partitioning on a 2,000-row table
-- would create more metadata overhead than it saves in scan cost, and
-- Iceberg's hidden partitioning means queries don't need the partition
-- column in the predicate anyway. The silver layer IS partitioned
-- (jurisdiction/year), which is where partition pruning actually pays
-- off, because that's the layer holding the full document text.
-- jurisdiction_code and year_partition are still carried as columns for
-- filtering; they're just not physical partitions here.

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

        -- Foreign keys to the dimensions.
        --
        -- jurisdiction_key and court_key are hashed inline: the natural
        -- key is already present on the staged row, so re-deriving the
        -- hash is exact and avoids a join. dim_court/dim_jurisdiction
        -- are still connected in the DAG because agg_judgments_by_court_year
        -- refs them.
        --
        -- date_key is resolved by an actual LEFT JOIN to dim_date rather
        -- than by hashing s.decision_date inline. Hashing would produce
        -- an identical value — but it would leave dim_date with no edge
        -- in the lineage graph, so dbt wouldn't know to build it first
        -- and a reviewer would see an orphaned dimension. Joining makes
        -- the dependency real and the DAG honest.
        --
        -- coalesce onto the explicit unknown/not-applicable members so no
        -- FK is ever null — that's what lets the relationships tests be
        -- strict rather than tolerating nulls.
        {{ dbt_utils.generate_surrogate_key(['s.jurisdiction_code']) }} as jurisdiction_key,

        coalesce(
            {{ dbt_utils.generate_surrogate_key(['s.court_name']) }},
            {{ dbt_utils.generate_surrogate_key(["'__not_applicable__'"]) }}
        ) as court_key,

        coalesce(
            d.date_key,
            {{ dbt_utils.generate_surrogate_key(["'__unknown__'"]) }}
        ) as date_key,

        s.doc_type,
        s.decision_date,
        s.decision_year,

        -- Measures.
        s.text_length,
        s.word_count,

        s.ingested_at,
        s.jurisdiction_code,
        s.year_partition

    from staged s

    -- LEFT, not INNER: a document whose decision_date falls outside the
    -- spine's 1900-2030 bounds must still land in the fact table (with
    -- the unknown date_key), not vanish from it. An inner join here would
    -- silently drop rows and break the bronze = silver + rejected
    -- reconciliation in a way that's very hard to trace back.
    left join {{ ref('dim_date') }} d
        on s.decision_date = d.full_date

)

select * from final
