-- Intermediate: hourly aggregated vitals per patient
select
    date_trunc('hour', reading_time)  as reading_hour,
    patient_id,
    min(unit)                         as unit,
    avg(heart_rate)                   as hr_avg,
    min(heart_rate)                   as hr_min,
    max(heart_rate)                   as hr_max,
    stddev(heart_rate)                as hr_stddev,
    avg(systolic_bp)                  as sbp_avg,
    min(systolic_bp)                  as sbp_min,
    avg(spo2)                         as spo2_avg,
    min(spo2)                         as spo2_min,
    avg(respiratory_rate)             as rr_avg,
    avg(temperature)                  as temp_avg,
    max(temperature)                  as temp_max,
    avg(mews_score)                   as mews_avg,
    max(mews_score)                   as mews_max,
    count(*)                          as reading_count
from {{ ref('stg_vitals_scored') }}
group by 1, 2
