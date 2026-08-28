-- Staging: MEWS-scored readings with severity classification
with source as (
    select * from {{ source('vitals', 'vitals_scored') }}
),

cleaned as (
    select
        time                as reading_time,
        trim(patient_id)    as patient_id,
        heart_rate,
        systolic_bp,
        diastolic_bp,
        respiratory_rate,
        spo2,
        temperature,
        upper(trim(avpu))   as avpu,
        mews_score,
        mews_hr,
        mews_sbp,
        mews_rr,
        mews_temp,
        mews_avpu,
        case
            when mews_score >= 5 then 'critical'
            when mews_score >= 3 then 'warning'
            else 'normal'
        end                 as severity,
        trim(device_id)     as device_id,
        trim(unit)          as unit
    from source
    where patient_id is not null
      and time is not null
)

select * from cleaned
