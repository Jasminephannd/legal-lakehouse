{{ config(materialized='table') }}

-- Grain: one row per (court, year). The obviously-useful mart — this is
-- the model that answers "which courts produce the most, and longest,
-- judgments, and how has that changed" without the reader writing a join.

with facts as (

    select * from {{ ref('fct_judgment') }}

),

joined as (

    select
        c.court_name,
        c.court_level,
        j.jurisdiction_name,
        j.jurisdiction_code,
        f.decision_year,
        f.text_length,
        f.word_count
    from facts f
    inner join {{ ref('dim_court') }} c
        on f.court_key = c.court_key
    inner join {{ ref('dim_jurisdiction') }} j
        on f.jurisdiction_key = j.jurisdiction_key
    where c.court_level != 'not_applicable'

)

select
    court_name,
    court_level,
    jurisdiction_name,
    jurisdiction_code,
    decision_year,
    count(*)                          as judgment_count,
    round(avg(text_length), 0)        as avg_text_length,
    round(avg(word_count), 0)         as avg_word_count,
    min(text_length)                  as min_text_length,
    max(text_length)                  as max_text_length
from joined
group by 1, 2, 3, 4, 5
