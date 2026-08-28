"""Statistical anomaly detection for patient vital signs.

Implements three complementary anomaly detection rules from statistical
process control (SPC) theory, adapted for continuous patient monitoring:

1. Shewhart Rule: Flag readings > 3 sigma from the patient's rolling mean
2. Rolling Z-Score: Per-patient running statistics over a configurable window
3. Trend Detection: Flag 5+ consecutive monotonic readings (sustained drift)

Each patient maintains independent running statistics, so what's normal for
one patient doesn't set the threshold for another. This per-patient baseline
approach is critical in clinical settings where inter-patient variability is
far larger than intra-patient variability.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

VITAL_PARAMS = [
    "heart_rate", "systolic_bp", "diastolic_bp",
    "respiratory_rate", "spo2", "temperature",
]


@dataclass
class Anomaly:
    """A detected anomaly in a patient's vital signs."""
    patient_id: str
    parameter: str
    value: float
    z_score: float
    rule_violated: str  # "shewhart", "trend", "range"
    timestamp: str
    message: str


@dataclass
class PatientStats:
    """Running statistics for a single patient's vital signs."""
    window_size: int = 30
    values: dict[str, deque] = field(default_factory=dict)
    trend_direction: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))

    def __post_init__(self) -> None:
        # deque maxlen must come from window_size, so the rolling window
        # actually honors the configured size
        self.values = defaultdict(lambda: deque(maxlen=self.window_size))

    def update(self, param: str, value: float) -> None:
        self.values[param].append(value)

    def mean(self, param: str) -> float:
        vals = self.values[param]
        return float(np.mean(vals)) if vals else 0.0

    def std(self, param: str) -> float:
        vals = self.values[param]
        return float(np.std(vals)) if len(vals) >= 2 else 1.0

    def z_score(self, param: str, value: float) -> float:
        std = self.std(param)
        if std < 1e-6:
            return 0.0
        return (value - self.mean(param)) / std

    def count(self, param: str) -> int:
        return len(self.values[param])


class VitalsAnomalyDetector:
    """Detect anomalies in patient vital sign streams."""

    def __init__(
        self,
        window_size: int = 30,
        z_threshold: float = 3.0,
        trend_length: int = 5,
    ):
        self.window_size = window_size
        self.z_threshold = z_threshold
        self.trend_length = trend_length
        self._patient_stats: dict[str, PatientStats] = {}

    def update(self, patient_id: str, vitals: dict) -> list[Anomaly]:
        """Process a new vitals reading and return any detected anomalies.

        Parameters
        ----------
        patient_id : unique patient identifier
        vitals : dict with vital sign values

        Returns
        -------
        list of Anomaly objects for any flagged parameters
        """
        if patient_id not in self._patient_stats:
            self._patient_stats[patient_id] = PatientStats(
                window_size=self.window_size
            )

        stats = self._patient_stats[patient_id]
        timestamp = vitals.get("timestamp", datetime.now(timezone.utc).isoformat())
        anomalies = []

        for param in VITAL_PARAMS:
            value = vitals.get(param)
            if value is None:
                continue

            value = float(value)

            # Only apply Shewhart after we have enough baseline data
            if stats.count(param) >= 10:
                z = stats.z_score(param, value)

                # Shewhart rule: |z| > threshold
                if abs(z) > self.z_threshold:
                    anomalies.append(Anomaly(
                        patient_id=patient_id,
                        parameter=param,
                        value=value,
                        z_score=round(z, 2),
                        rule_violated="shewhart",
                        timestamp=timestamp,
                        message=(
                            f"{param}={value:.1f} is {abs(z):.1f} sigma from "
                            f"patient baseline (mean={stats.mean(param):.1f}, "
                            f"sd={stats.std(param):.1f})"
                        ),
                    ))

            # Trend detection: track direction of consecutive changes
            trend = stats.trend_direction[param]
            if stats.count(param) > 0:
                last_val = stats.values[param][-1]
                if value > last_val:
                    trend.append(1)
                elif value < last_val:
                    trend.append(-1)
                else:
                    trend.append(0)

                # Keep only recent trend data
                if len(trend) > self.trend_length + 5:
                    stats.trend_direction[param] = trend[-self.trend_length - 5:]
                    trend = stats.trend_direction[param]

                # Check for monotonic trend. Fire once per sustained run:
                # a 40-reading climb is one event, not 35 duplicate alerts,
                # so the direction buffer resets after each detection.
                if len(trend) >= self.trend_length:
                    recent = trend[-self.trend_length:]
                    direction = None
                    if all(d == 1 for d in recent):
                        direction = "rising"
                    elif all(d == -1 for d in recent):
                        direction = "falling"
                    if direction is not None:
                        anomalies.append(Anomaly(
                            patient_id=patient_id,
                            parameter=param,
                            value=value,
                            z_score=stats.z_score(param, value) if stats.count(param) >= 2 else 0.0,
                            rule_violated="trend",
                            timestamp=timestamp,
                            message=(
                                f"{param} has been {direction} for {self.trend_length} "
                                f"consecutive readings (current={value:.1f})"
                            ),
                        ))
                        stats.trend_direction[param] = []

            # Update running stats AFTER anomaly check
            stats.update(param, value)

        return anomalies

    def get_patient_stats(self, patient_id: str) -> PatientStats | None:
        return self._patient_stats.get(patient_id)

    @property
    def monitored_patients(self) -> list[str]:
        return list(self._patient_stats.keys())
