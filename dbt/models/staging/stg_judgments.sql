{{ config(materialized='view') }}

-- Staging: renaming, casting and trimming only. No joins, no business
-- logic, no aggregation — that convention is what a dbt-experienced
-- reviewer looks for first.
--
-- The casting here is deliberate: the Glue table types decision_date and
-- ingested_at as strings, because Pydantic's model_dump(mode='json')
-- serialises them to ISO-8601 and pyarrow infers from the data. Casting
-- belongs in staging, not in the raw source definition.

with source as (

    select * from {{ source('silver', 'silver_judgments') }}

),

renamed as (

    select
        doc_id,

        -- Standardise: lowercase codes, consistent nulls.
        lower(trim(jurisdiction))                       as jurisdiction_code,
        lower(trim(doc_type))                           as doc_type,

        -- court is null for legislation/bills by design. nullif guards
        -- against empty strings sneaking in as a distinct "value".
        nullif(trim(court), '')                         as court_name,

        nullif(trim(citation), '')                      as citation,

        -- ISO date string -> real date. try() returns null rather than
        -- failing the whole query on an unparseable value.
        try(date(from_iso8601_timestamp(decision_date))) as decision_date,

        -- 'unknown' is a real, expected partition value — keep it as a
        -- string rather than coercing to an integer and losing it.
        year                                            as year_partition,
        try_cast(nullif(year, 'unknown') as integer)    as decision_year,

        source_url,
        text,
        cast(text_length as bigint)                     as text_length,

        -- Cheap proxy measure; whitespace-split word count.
        cardinality(split(text, ' '))                   as word_count,

        from_iso8601_timestamp(ingested_at)             as ingested_at

    from source

)

select * from renamed
