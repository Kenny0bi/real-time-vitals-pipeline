-- One row per patient: latest vitals, max MEWS, alert count, session stats
with latest_vitals as (
    select distinct on (patient_id)
        patient_id,
        reading_time as last_reading_time,
        heart_rate,
        systolic_bp,
        diastolic_bp,
        respiratory_rate,
        spo2,
        temperature,
        avpu,
        mews_score,
        severity,
        unit,
        device_id
    from {{ ref('stg_vitals_scored') }}
    order by patient_id, reading_time desc
),

patient_stats as (
    select
        patient_id,
        count(*) as total_readings,
        max(mews_score) as max_mews,
        avg(mews_score) as avg_mews,
        min(reading_time) as first_reading,
        max(reading_time) as last_reading
    from {{ ref('stg_vitals_scored') }}
    group by patient_id
),

session_stats as (
    select
        patient_id,
        count(*) as session_count,
        sum(session_hours) as total_monitoring_hours
    from {{ ref('int_patient_sessions') }}
    group by patient_id
),

alert_counts as (
    select
        patient_id,
        count(*) as alert_count,
        count(*) filter (where severity = 'critical') as critical_alert_count,
        max(alert_time) as last_alert_time
    from {{ ref('stg_alerts') }}
    group by patient_id
)

select
    lv.patient_id,
    lv.unit,
    lv.device_id,
    lv.last_reading_time,
    lv.heart_rate as latest_hr,
    lv.systolic_bp as latest_sbp,
    lv.spo2 as latest_spo2,
    lv.respiratory_rate as latest_rr,
    lv.temperature as latest_temp,
    lv.avpu as latest_avpu,
    lv.mews_score as current_mews,
    lv.severity as status,
    ps.total_readings,
    ps.max_mews,
    round(ps.avg_mews::numeric, 1) as avg_mews,
    coalesce(ss.session_count, 0) as session_count,
    round(coalesce(ss.total_monitoring_hours, 0)::numeric, 1)
        as monitoring_hours,
    coalesce(ac.alert_count, 0) as alert_count,
    coalesce(ac.critical_alert_count, 0) as critical_alert_count,
    ac.last_alert_time
from latest_vitals lv
left join patient_stats ps on lv.patient_id = ps.patient_id
left join session_stats ss on lv.patient_id = ss.patient_id
left join alert_counts ac on lv.patient_id = ac.patient_id
