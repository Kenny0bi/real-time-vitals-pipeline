"""Data layer for the dashboard: live TimescaleDB or in-process demo.

Two sources behind one interface:

- ``LiveData`` queries TimescaleDB, i.e. whatever the streaming pipeline
  has actually written. Requires the Docker infrastructure.
- ``DemoData`` runs the real simulator and the real stream-processing core
  (``VitalsProcessor``) inside the Streamlit process. Not mocks: the same
  MEWS calculator, the same anomaly detector, the same alert rules. It
  exists so anyone can see the dashboard working with nothing installed
  but Python.

Selection: VITALS_DEMO_MODE=1 forces demo; otherwise the app tries the
database and falls back to demo with a banner.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..ingestion.vitals_simulator import (
    DeteriorationPattern,
    PatientProfile,
    VitalsSimulator,
)
from ..processing.stream_worker import VitalsProcessor

HISTORY_SECONDS = 1800  # 30 minutes of per-patient history

# The demo ward: mostly stable patients plus four deterioration stories at
# different points in their arc (pre-aged via warm), so the dashboard is
# interesting the moment it opens.
_DEMO_CAST = [
    ("none", 0.0, 0),
    ("sepsis", 0.050, 800),
    ("none", 0.0, 0),
    ("none", 0.0, 0),
    ("respiratory_failure", 0.045, 660),
    ("none", 0.0, 0),
    ("cardiac_event", 0.055, 380),
    ("none", 0.0, 0),
    ("none", 0.0, 0),
    ("sepsis", 0.035, 120),
    ("none", 0.0, 0),
    ("none", 0.0, 0),
    ("none", 0.0, 0),
    ("respiratory_failure", 0.040, 300),
    ("none", 0.0, 0),
    ("none", 0.0, 0),
    ("cardiac_event", 0.045, 150),
    ("none", 0.0, 0),
    ("none", 0.0, 0),
    ("none", 0.0, 0),
]

_UNITS = ["ICU-2A", "ICU-2B", "CCU-1", "STEP-DOWN-3", "MED-SURG-4"]


@dataclass
class DemoState:
    """Everything the demo needs to persist across Streamlit reruns."""

    simulator: VitalsSimulator
    processor: VitalsProcessor
    history: dict[str, deque] = field(default_factory=dict)
    alerts: list[dict] = field(default_factory=list)
    throughput: deque = field(default_factory=lambda: deque(maxlen=120))
    last_step: float = field(default_factory=time.time)
    started: float = field(default_factory=time.time)


def _build_demo_state(seed: int = 42) -> DemoState:
    sim = VitalsSimulator(num_patients=0, seed=seed)
    for i, (pattern, rate, warm) in enumerate(_DEMO_CAST):
        unit = _UNITS[i % len(_UNITS)]
        profile = PatientProfile(
            patient_id=f"PT-{i + 1:05d}",
            unit=unit,
            device_id=f"MON-{unit}-{i:02d}",
            deterioration=DeteriorationPattern(pattern),
            deterioration_rate=rate,
        )
        profile.readings_generated = warm
        sim.patients.append(profile)

    state = DemoState(simulator=sim, processor=VitalsProcessor())
    for p in sim.patients:
        state.history[p.patient_id] = deque(maxlen=HISTORY_SECONDS)

    # replay six minutes of back-story so charts open with history
    for p in sim.patients:
        p.readings_generated = max(0, p.readings_generated - 360)
    for _ in range(360):
        _advance_one(state)
    state.alerts = state.alerts[-40:]
    return state


def _advance_one(state: DemoState) -> None:
    """One simulated second: every patient produces one scored reading."""
    for p in state.simulator.patients:
        reading = state.simulator.generate_reading(p)
        result = state.processor.process(reading)
        state.history[p.patient_id].append(result.scored_record)
        state.alerts.extend(result.alerts)
    if len(state.alerts) > 400:
        state.alerts = state.alerts[-400:]


class DemoData:
    """In-process simulation source. Call advance() once per rerun."""

    source_name = "demo"

    def __init__(self, state: DemoState):
        self.state = state

    def advance(self) -> None:
        """Catch the simulation up to wall-clock time (max 30 s per rerun)."""
        now = time.time()
        steps = min(30, int(now - self.state.last_step))
        for _ in range(steps):
            _advance_one(self.state)
        if steps:
            self.state.last_step = now
            self.state.throughput.append(
                (datetime.now(timezone.utc),
                 steps * len(self.state.simulator.patients))
            )

    # -- interface used by the app --

    def patients_latest(self) -> list[dict]:
        out = []
        for hist in self.state.history.values():
            if hist:
                out.append(hist[-1])
        return sorted(out, key=lambda r: r["patient_id"])

    def patient_history(self, patient_id: str, seconds: int = 1800) -> list[dict]:
        hist = self.state.history.get(patient_id, [])
        return list(hist)[-seconds:]

    def alerts(self, limit: int = 100) -> list[dict]:
        return list(reversed(self.state.alerts[-limit:]))

    def units(self) -> list[str]:
        return sorted({p.unit for p in self.state.simulator.patients})

    def system_stats(self) -> dict:
        proc = self.state.processor
        return {
            "readings_processed": proc.processed_count,
            "alerts_generated": proc.alert_count,
            "patients": len(self.state.simulator.patients),
            "uptime_sec": int(time.time() - self.state.started),
            "throughput": list(self.state.throughput),
        }


class LiveData:
    """TimescaleDB source: reads what the real pipeline wrote."""

    source_name = "timescaledb"

    def __init__(self):
        import psycopg2  # optional dependency, only needed in live mode

        from ..config.settings import settings
        self._conn = psycopg2.connect(settings.timescale_url, connect_timeout=3)
        self._conn.autocommit = True

    def advance(self) -> None:  # the pipeline advances itself
        pass

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def patients_latest(self) -> list[dict]:
        return self._rows("""
            SELECT DISTINCT ON (patient_id) patient_id,
                   time AS timestamp, heart_rate, systolic_bp, diastolic_bp,
                   respiratory_rate, spo2, temperature, avpu, mews_score,
                   mews_hr, mews_sbp, mews_rr, mews_temp, mews_avpu,
                   device_id, unit
            FROM vitals_scored ORDER BY patient_id, time DESC
        """)

    def patient_history(self, patient_id: str, seconds: int = 1800) -> list[dict]:
        return self._rows("""
            SELECT time AS timestamp, heart_rate, systolic_bp, diastolic_bp,
                   respiratory_rate, spo2, temperature, avpu, mews_score,
                   mews_hr, mews_sbp, mews_rr, mews_temp, mews_avpu
            FROM vitals_scored
            WHERE patient_id = %s AND time > now() - make_interval(secs => %s)
            ORDER BY time
        """, (patient_id, seconds))

    def alerts(self, limit: int = 100) -> list[dict]:
        return self._rows("""
            SELECT time AS timestamp, patient_id, alert_type, severity,
                   mews_score, message
            FROM alerts ORDER BY time DESC LIMIT %s
        """, (limit,))

    def units(self) -> list[str]:
        rows = self._rows(
            "SELECT DISTINCT unit FROM vitals_scored WHERE unit IS NOT NULL"
        )
        return sorted(r["unit"] for r in rows)

    def system_stats(self) -> dict:
        row = self._rows("""
            SELECT
                (SELECT count(*) FROM vitals_scored) AS readings_processed,
                (SELECT count(*) FROM alerts) AS alerts_generated,
                (SELECT count(DISTINCT patient_id) FROM vitals_scored) AS patients,
                (SELECT count(*) FROM vitals_scored
                 WHERE time > now() - interval '60 seconds') AS last_minute,
                pg_database_size(current_database())/1024.0/1024.0 AS db_mb
        """)[0]
        row["readings_per_sec"] = round(row.pop("last_minute") / 60.0, 2)
        row["db_size_mb"] = round(float(row.pop("db_mb")), 1)
        row["throughput"] = []
        return row
