-- Staging: clean and standardize alert records
with source as (
    select * from {{ source('vitals', 'alerts') }}
),

cleaned as (
    select
        id                          as alert_id,
        time                        as alert_time,
        trim(patient_id)            as patient_id,
        lower(trim(alert_type))     as alert_type,
        lower(trim(severity))       as severity,
        mews_score,
        message,
        coalesce(acknowledged, false) as acknowledged,
        acknowledged_by,
        acknowledged_at,
        -- time from alert to acknowledgement, when acknowledged
        case
            when acknowledged_at is not null
            then extract(epoch from acknowledged_at - time)
        end                         as seconds_to_acknowledge
    from source
    where patient_id is not null
      and time is not null
)

select * from cleaned
