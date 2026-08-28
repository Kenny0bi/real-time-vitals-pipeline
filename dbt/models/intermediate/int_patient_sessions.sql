-- Intermediate: monitoring sessions per patient.
-- A gap of more than 30 minutes between consecutive readings starts a new
-- session (patient transferred, discharged, or monitor disconnected).
with readings as (
    select
        patient_id,
        reading_time,
        unit,
        mews_score,
        lag(reading_time) over (
            partition by patient_id order by reading_time
        ) as prev_reading_time
    from {{ ref('stg_vitals_scored') }}
),

flagged as (
    select
        *,
        case
            when prev_reading_time is null then 1
            when reading_time - prev_reading_time > interval '30 minutes' then 1
            else 0
        end as is_session_start
    from readings
),

numbered as (
    select
        *,
        sum(is_session_start) over (
            partition by patient_id order by reading_time
            rows between unbounded preceding and current row
        ) as session_number
    from flagged
)

select
    patient_id,
    session_number,
    min(unit)                as unit,
    min(reading_time)        as session_start,
    max(reading_time)        as session_end,
    count(*)                 as reading_count,
    max(mews_score)          as max_mews,
    round(avg(mews_score), 2) as avg_mews,
    extract(epoch from max(reading_time) - min(reading_time)) / 3600.0
                             as session_hours
from numbered
group by patient_id, session_number
