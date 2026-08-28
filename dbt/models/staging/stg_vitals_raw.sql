-- Staging: clean and type-cast raw vitals readings
with source as (
    select * from {{ source('vitals', 'vitals_raw') }}
),

cleaned as (
    select
        time                                        as reading_time,
        trim(patient_id)                            as patient_id,
        heart_rate,
        systolic_bp,
        diastolic_bp,
        respiratory_rate,
        spo2,
        temperature,
        upper(trim(avpu))                           as avpu,
        trim(device_id)                             as device_id,
        trim(unit)                                  as unit,
        -- Data quality flags
        case when heart_rate between 20 and 300 then true else false end as hr_valid,
        case when systolic_bp between 30 and 300 then true else false end as sbp_valid,
        case when spo2 between 50 and 100 then true else false end as spo2_valid,
        case when respiratory_rate between 4 and 60 then true else false end as rr_valid,
        case when temperature between 30 and 45 then true else false end as temp_valid
    from source
    where patient_id is not null
      and time is not null
)

select * from cleaned
