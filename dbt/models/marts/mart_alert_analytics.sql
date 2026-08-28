-- Alert analysis: frequency by type, time-to-acknowledge, MEWS at alert time
with alerts as (
    select * from {{ ref('stg_alerts') }}
)

select
    alert_type,
    severity,
    count(*)                                        as alert_count,
    count(distinct patient_id)                      as patients_affected,
    round(avg(mews_score)::numeric, 2)              as avg_mews_at_alert,
    max(mews_score)                                 as max_mews_at_alert,
    percentile_cont(0.5) within group (order by mews_score)
                                                    as median_mews_at_alert,
    count(*) filter (where acknowledged)            as acknowledged_count,
    round(
        count(*) filter (where acknowledged)::numeric
            / nullif(count(*), 0), 2
    )                                               as acknowledge_rate,
    round(
        avg(seconds_to_acknowledge) filter (where acknowledged)::numeric, 0
    )                                               as avg_seconds_to_acknowledge,
    min(alert_time)                                 as first_alert,
    max(alert_time)                                 as last_alert
from alerts
group by alert_type, severity
order by alert_count desc
