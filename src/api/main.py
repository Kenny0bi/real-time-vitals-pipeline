"""FastAPI application for the Real-Time Patient Vitals API.

REST and WebSocket endpoints for patient vitals, MEWS scores, alerts, and
unit-level dashboard data. Historical queries hit TimescaleDB (hypertables
and continuous aggregates); the live WebSocket feed subscribes to the Redis
pub/sub channel that the stream worker publishes to.

The app starts cleanly even when the infrastructure is down: data endpoints
return 503 with an actionable message until TimescaleDB is reachable.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..config.settings import settings
from . import db

logger = logging.getLogger(__name__)

API_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.disconnect()


app = FastAPI(
    title="Real-Time Patient Vitals API",
    description=(
        "REST and WebSocket API for monitoring patient vital signs, "
        "MEWS scores, and clinical deterioration alerts."
    ),
    version=API_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": API_VERSION,
        "database": "connected" if db._pool is not None else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/patients")
async def list_patients(
    unit: str | None = Query(None, description="Filter by unit"),
    status: str | None = Query(
        None, description="Filter by MEWS status: normal/warning/critical"
    ),
):
    """List all monitored patients with their latest vitals and MEWS score."""
    patients = await db.latest_patients(unit=unit, status=status)
    return {
        "patients": patients,
        "total": len(patients),
        "filters": {"unit": unit, "status": status},
    }


@app.get("/api/v1/patients/{patient_id}/vitals")
async def patient_vitals(
    patient_id: str,
    start: datetime | None = Query(None, description="Start timestamp (ISO)"),
    end: datetime | None = Query(None, description="End timestamp (ISO)"),
    interval: str = Query("raw", description="Aggregation: raw, 5min, 1hr"),
    limit: int = Query(500, ge=1, le=5000),
):
    """Historical vitals from the hypertable or a continuous aggregate."""
    readings = await db.patient_vitals(patient_id, start, end, interval, limit)
    return {
        "patient_id": patient_id,
        "interval": interval,
        "readings": readings,
        "total": len(readings),
    }


@app.get("/api/v1/patients/{patient_id}/mews")
async def patient_mews(
    patient_id: str,
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
):
    """MEWS score history with per-parameter breakdown."""
    scores = await db.patient_mews_history(patient_id, start, end, limit)
    current = scores[0] if scores else None
    return {
        "patient_id": patient_id,
        "scores": scores,
        "current_mews": current["mews_score"] if current else None,
        "severity": db._severity(current["mews_score"]) if current else None,
    }


@app.get("/api/v1/patients/{patient_id}/alerts")
async def patient_alerts(
    patient_id: str,
    severity: str | None = Query(None),
    acknowledged: bool | None = Query(None),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """Alert history for a patient with filtering."""
    alerts = await db.patient_alerts(
        patient_id, severity, acknowledged, start, end, limit
    )
    return {"patient_id": patient_id, "alerts": alerts, "total": len(alerts)}


@app.get("/api/v1/alerts")
async def all_alerts(
    severity: str | None = Query(None),
    acknowledged: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    """Alert feed across all patients (dashboard timeline view)."""
    alerts = await db.patient_alerts(
        None, severity, acknowledged, None, None, limit
    )
    return {"alerts": alerts, "total": len(alerts)}


@app.get("/api/v1/units/{unit}/overview")
async def unit_overview(unit: str):
    """All patients in a unit with MEWS status counts (bed-map view)."""
    return await db.unit_overview(unit)


@app.get("/api/v1/analytics/trends")
async def analytics_trends(
    unit: str | None = Query(None),
    interval: str = Query("1hr", description="Aggregation: 5min, 1hr"),
    hours: int = Query(24, ge=1, le=168),
):
    """Aggregate vital sign trends from continuous aggregates."""
    trends = await db.analytics_trends(unit, interval, hours)
    return {"interval": interval, "hours": hours, "trends": trends}


@app.get("/api/v1/pipeline/status")
async def pipeline_status():
    """Pipeline health: data freshness, throughput, database size."""
    return await db.pipeline_status()


@app.websocket("/api/v1/ws/vitals/{patient_id}")
async def websocket_vitals(websocket: WebSocket, patient_id: str):
    """Live vitals stream for one patient via Redis pub/sub.

    The stream worker publishes every scored reading to the channel
    ``vitals:{patient_id}``; this endpoint forwards those frames to the
    connected client as JSON text.
    """
    await websocket.accept()

    try:
        import redis.asyncio as aioredis
        r = aioredis.Redis(
            host=settings.redis_host, port=settings.redis_port,
            socket_connect_timeout=2,
        )
        pubsub = r.pubsub()
        await pubsub.subscribe(f"vitals:{patient_id}")
    except Exception as exc:  # noqa: BLE001
        await websocket.send_json({
            "error": f"Redis unavailable, live feed disabled: {exc}",
        })
        await websocket.close()
        return

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=5.0
            )
            if message is not None:
                await websocket.send_text(message["data"].decode())
            else:
                # keepalive so proxies do not drop the idle connection
                await websocket.send_json({"type": "keepalive"})
            await asyncio.sleep(0)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(f"vitals:{patient_id}")
        await r.aclose()
