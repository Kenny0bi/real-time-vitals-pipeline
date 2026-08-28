"""Database access layer for the Vitals API.

Manages a single asyncpg pool for the app lifetime and provides the SQL
for every endpoint in one place. If TimescaleDB is unreachable the pool
stays None and endpoints raise a clean 503 instead of a stack trace, so
the API can start before the infrastructure does.
"""

from __future__ import annotations

import logging
from datetime import datetime

import asyncpg
from fastapi import HTTPException

from ..config.settings import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# Aggregate views per interval: (relation, time column)
_INTERVAL_SOURCES = {
    "raw": ("vitals_scored", "time"),
    "5min": ("vitals_5min", "bucket"),
    "1hr": ("vitals_1hr", "bucket"),
}


async def connect() -> None:
    """Create the pool at startup; tolerate the database being down."""
    global _pool
    try:
        _pool = await asyncpg.create_pool(
            dsn=settings.timescale_url, min_size=2, max_size=10, timeout=5,
        )
        logger.info("Connected to TimescaleDB")
    except Exception as exc:  # noqa: BLE001 - startup must not crash
        logger.warning(f"TimescaleDB unavailable at startup: {exc}")
        _pool = None


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the live pool or raise 503 if the database is down."""
    if _pool is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "TimescaleDB is not reachable. Start the infrastructure "
                "(docker compose up -d) and restart the API."
            ),
        )
    return _pool


def _severity(mews: int | None) -> str:
    if mews is None:
        return "unknown"
    if mews >= 5:
        return "critical"
    if mews >= 3:
        return "warning"
    return "normal"


async def latest_patients(
    unit: str | None = None, status: str | None = None
) -> list[dict]:
    """Most recent scored reading per patient, with MEWS status."""
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (patient_id)
            patient_id, time, heart_rate, systolic_bp, diastolic_bp,
            respiratory_rate, spo2, temperature, avpu, mews_score,
            mews_hr, mews_sbp, mews_rr, mews_temp, mews_avpu,
            device_id, unit
        FROM vitals_scored
        WHERE ($1::text IS NULL OR unit = $1)
        ORDER BY patient_id, time DESC
        """,
        unit,
    )
    patients = []
    for r in rows:
        s = _severity(r["mews_score"])
        if status and s != status:
            continue
        patients.append({**dict(r), "status": s, "last_seen": r["time"]})
    return patients


async def patient_vitals(
    patient_id: str,
    start: datetime | None,
    end: datetime | None,
    interval: str,
    limit: int,
) -> list[dict]:
    """Historical vitals from the raw hypertable or a continuous aggregate."""
    if interval not in _INTERVAL_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"interval must be one of {sorted(_INTERVAL_SOURCES)}",
        )
    relation, time_col = _INTERVAL_SOURCES[interval]
    pool = get_pool()
    rows = await pool.fetch(
        f"""
        SELECT * FROM {relation}
        WHERE patient_id = $1
          AND ($2::timestamptz IS NULL OR {time_col} >= $2)
          AND ($3::timestamptz IS NULL OR {time_col} <= $3)
        ORDER BY {time_col} DESC
        LIMIT $4
        """,
        patient_id, start, end, limit,
    )
    return [dict(r) for r in rows]


async def patient_mews_history(
    patient_id: str,
    start: datetime | None,
    end: datetime | None,
    limit: int,
) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT time, mews_score, mews_hr, mews_sbp, mews_rr,
               mews_temp, mews_avpu
        FROM vitals_scored
        WHERE patient_id = $1
          AND ($2::timestamptz IS NULL OR time >= $2)
          AND ($3::timestamptz IS NULL OR time <= $3)
        ORDER BY time DESC
        LIMIT $4
        """,
        patient_id, start, end, limit,
    )
    return [dict(r) for r in rows]


async def patient_alerts(
    patient_id: str | None,
    severity: str | None,
    acknowledged: bool | None,
    start: datetime | None,
    end: datetime | None,
    limit: int,
) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT id, time, patient_id, alert_type, severity, mews_score,
               message, acknowledged
        FROM alerts
        WHERE ($1::text IS NULL OR patient_id = $1)
          AND ($2::text IS NULL OR severity = $2)
          AND ($3::boolean IS NULL OR acknowledged = $3)
          AND ($4::timestamptz IS NULL OR time >= $4)
          AND ($5::timestamptz IS NULL OR time <= $5)
        ORDER BY time DESC
        LIMIT $6
        """,
        patient_id, severity, acknowledged, start, end, limit,
    )
    return [dict(r) for r in rows]


async def unit_overview(unit: str) -> dict:
    patients = await latest_patients(unit=unit)
    summary = {"normal": 0, "warning": 0, "critical": 0}
    for p in patients:
        if p["status"] in summary:
            summary[p["status"]] += 1
    return {
        "unit": unit,
        "patients": patients,
        "total": len(patients),
        "summary": summary,
    }


async def analytics_trends(
    unit: str | None, interval: str, hours: int
) -> list[dict]:
    """Unit-wide averages over time from the continuous aggregates."""
    relation = "vitals_1hr" if interval == "1hr" else "vitals_5min"
    pool = get_pool()
    rows = await pool.fetch(
        f"""
        SELECT v.bucket,
               avg(v.hr_avg)   AS hr_avg,
               avg(v.sbp_avg)  AS sbp_avg,
               avg(v.spo2_avg) AS spo2_avg,
               avg(v.rr_avg)   AS rr_avg,
               avg(v.temp_avg) AS temp_avg,
               sum(v.reading_count) AS reading_count
        FROM {relation} v
        WHERE v.bucket > now() - make_interval(hours => $1)
        GROUP BY v.bucket
        ORDER BY v.bucket
        """,
        hours,
    )
    return [dict(r) for r in rows]


async def pipeline_status() -> dict:
    """Freshness and volume metrics straight from the database."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            (SELECT count(*) FROM vitals_scored) AS scored_total,
            (SELECT count(*) FROM alerts) AS alert_total,
            (SELECT max(time) FROM vitals_scored) AS latest_reading,
            (SELECT count(*) FROM vitals_scored
             WHERE time > now() - interval '60 seconds') AS readings_last_minute,
            pg_database_size(current_database()) / 1024.0 / 1024.0 AS db_size_mb
        """
    )
    latest = row["latest_reading"]
    return {
        "scored_total": row["scored_total"],
        "alert_total": row["alert_total"],
        "latest_reading": latest,
        "readings_per_sec": round(row["readings_last_minute"] / 60.0, 2),
        "db_size_mb": round(float(row["db_size_mb"]), 1),
    }
