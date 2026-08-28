-- Unit-level aggregates: census, MEWS distribution, alert rate
with latest_per_patient as (
    select distinct on (patient_id)
        patient_id,
        unit,
        mews_score,
        severity,
        reading_time
    from {{ ref('stg_vitals_scored') }}
    order by patient_id, reading_time desc
),

unit_alerts as (
    select
        s.unit,
        count(a.alert_id) as alert_count_24h,
        count(a.alert_id) filter (where a.severity = 'critical')
            as critical_alerts_24h
    from {{ ref('stg_alerts') }} a
    join latest_per_patient s on a.patient_id = s.patient_id
    where a.alert_time > now() - interval '24 hours'
    group by s.unit
)

select
    p.unit,
    count(*)                                        as patient_count,
    round(avg(p.mews_score)::numeric, 2)            as avg_mews,
    max(p.mews_score)                               as max_mews,
    count(*) filter (where p.severity = 'normal')   as normal_count,
    count(*) filter (where p.severity = 'warning')  as warning_count,
    count(*) filter (where p.severity = 'critical') as critical_count,
    coalesce(ua.alert_count_24h, 0)                 as alert_count_24h,
    coalesce(ua.critical_alerts_24h, 0)             as critical_alerts_24h,
    round(
        coalesce(ua.alert_count_24h, 0)::numeric / count(*), 2
    )                                               as alerts_per_patient_24h
from latest_per_patient p
left join unit_alerts ua on p.unit = ua.unit
group by p.unit, ua.alert_count_24h, ua.critical_alerts_24h
