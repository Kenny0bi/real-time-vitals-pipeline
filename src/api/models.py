"""Pydantic models for the Vitals API request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class VitalsReading(BaseModel):
    patient_id: str
    timestamp: datetime
    heart_rate: float | None = None
    systolic_bp: float | None = None
    diastolic_bp: float | None = None
    respiratory_rate: float | None = None
    spo2: float | None = None
    temperature: float | None = None
    avpu: str | None = None
    device_id: str | None = None
    unit: str | None = None


class MEWSScore(BaseModel):
    total: int = Field(..., ge=0, le=15)
    hr_score: int = Field(..., ge=0, le=3)
    sbp_score: int = Field(..., ge=0, le=3)
    rr_score: int = Field(..., ge=0, le=3)
    temp_score: int = Field(..., ge=0, le=3)
    avpu_score: int = Field(..., ge=0, le=3)
    severity: str


class PatientSummary(BaseModel):
    patient_id: str
    unit: str
    last_seen: datetime | None
    latest_vitals: VitalsReading | None
    current_mews: MEWSScore | None
    alert_count: int = 0
    status: str = "normal"


class Alert(BaseModel):
    id: int
    timestamp: datetime
    patient_id: str
    alert_type: str
    severity: str
    mews_score: int | None
    message: str
    acknowledged: bool = False


class UnitOverview(BaseModel):
    unit: str
    patients: list[PatientSummary]
    total: int
    summary: dict[str, int]  # normal/warning/critical counts


class PipelineHealth(BaseModel):
    kafka_lag: int
    throughput_per_sec: float
    db_disk_mb: float
    processing_latency_p50_ms: float
    processing_latency_p99_ms: float
