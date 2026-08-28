"""Synthetic patient vitals generator with realistic clinical trajectories.

Generates continuous vital sign readings for N simulated patients, each with
unique physiological baselines and optional deterioration trajectories. The
simulator models three common clinical deterioration patterns:

  - Sepsis: rising HR/RR/temperature, falling BP
  - Respiratory failure: falling SpO2, rising RR/HR
  - Cardiac event: erratic HR, falling BP/SpO2

Each reading includes Gaussian noise around the patient's current baseline,
with circadian rhythm modulation for heart rate and blood pressure.

Usage:
    python -m src.ingestion.vitals_simulator --patients 20 --rate 1.0
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import click
import numpy as np

logger = logging.getLogger(__name__)

# Physiological baseline ranges for healthy patients
BASELINE_RANGES = {
    "heart_rate": (60, 100),
    "systolic_bp": (110, 140),
    "diastolic_bp": (60, 90),
    "respiratory_rate": (12, 20),
    "spo2": (95.0, 100.0),
    "temperature": (36.1, 37.2),
}

# Noise standard deviations per parameter
NOISE_STD = {
    "heart_rate": 3.0,
    "systolic_bp": 5.0,
    "diastolic_bp": 3.0,
    "respiratory_rate": 1.5,
    "spo2": 0.5,
    "temperature": 0.15,
}

UNITS = ["ICU-2A", "ICU-2B", "CCU-1", "STEP-DOWN-3", "MED-SURG-4"]
AVPU_VALUES = ["A", "V", "P", "U"]


class DeteriorationPattern(Enum):
    NONE = "none"
    SEPSIS = "sepsis"
    RESPIRATORY = "respiratory_failure"
    CARDIAC = "cardiac_event"


@dataclass
class PatientProfile:
    """Simulated patient with baseline vitals and optional deterioration."""
    patient_id: str
    unit: str
    device_id: str
    baselines: dict[str, float] = field(default_factory=dict)
    deterioration: DeteriorationPattern = DeteriorationPattern.NONE
    deterioration_rate: float = 0.0  # per-reading shift magnitude
    readings_generated: int = 0

    def __post_init__(self):
        if not self.baselines:
            self.baselines = {
                param: random.uniform(*rng)
                for param, rng in BASELINE_RANGES.items()
            }


class VitalsSimulator:
    """Generate realistic patient vital sign streams."""

    def __init__(
        self,
        num_patients: int = 20,
        deterioration_prob: float = 0.2,
        seed: int | None = None,
    ):
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

        self.patients: list[PatientProfile] = []
        deterioration_patterns = [
            DeteriorationPattern.SEPSIS,
            DeteriorationPattern.RESPIRATORY,
            DeteriorationPattern.CARDIAC,
        ]

        for i in range(num_patients):
            patient_id = f"PT-{i:05d}"
            unit = random.choice(UNITS)
            device_id = f"MON-{unit}-{i:02d}"

            pattern = DeteriorationPattern.NONE
            rate = 0.0
            if random.random() < deterioration_prob:
                pattern = random.choice(deterioration_patterns)
                rate = random.uniform(0.01, 0.05)

            self.patients.append(
                PatientProfile(
                    patient_id=patient_id,
                    unit=unit,
                    device_id=device_id,
                    deterioration=pattern,
                    deterioration_rate=rate,
                )
            )

    def generate_reading(self, patient: PatientProfile) -> dict:
        """Generate a single vitals reading for a patient."""
        patient.readings_generated += 1
        t = patient.readings_generated

        vitals = {}
        for param, baseline in patient.baselines.items():
            noise = np.random.normal(0, NOISE_STD[param])
            shift = self._deterioration_shift(patient, param, t)
            value = baseline + noise + shift
            vitals[param] = round(value, 1)

        # Clamp to physiological limits
        vitals["heart_rate"] = max(20, min(300, vitals["heart_rate"]))
        vitals["systolic_bp"] = max(30, min(300, vitals["systolic_bp"]))
        vitals["diastolic_bp"] = max(20, min(200, vitals["diastolic_bp"]))
        vitals["respiratory_rate"] = max(4, min(60, vitals["respiratory_rate"]))
        vitals["spo2"] = max(50, min(100, vitals["spo2"]))
        vitals["temperature"] = max(30, min(45, vitals["temperature"]))

        # AVPU — consciousness only degrades late in a deterioration arc,
        # gated on progress (rate * elapsed) rather than elapsed time alone
        avpu = "A"
        if (
            patient.deterioration != DeteriorationPattern.NONE
            and patient.deterioration_rate * t > 32
        ):
            avpu_probs = [0.72, 0.18, 0.08, 0.02]
            avpu = random.choices(AVPU_VALUES, weights=avpu_probs, k=1)[0]

        return {
            "patient_id": patient.patient_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "heart_rate": vitals["heart_rate"],
            "systolic_bp": vitals["systolic_bp"],
            "diastolic_bp": vitals["diastolic_bp"],
            "respiratory_rate": vitals["respiratory_rate"],
            "spo2": vitals["spo2"],
            "temperature": vitals["temperature"],
            "avpu": avpu,
            "device_id": patient.device_id,
            "unit": patient.unit,
        }

    def _deterioration_shift(
        self, patient: PatientProfile, param: str, t: int
    ) -> float:
        """Calculate the deterioration-induced shift for a parameter."""
        if patient.deterioration == DeteriorationPattern.NONE:
            return 0.0

        rate = patient.deterioration_rate * t

        shifts = {
            DeteriorationPattern.SEPSIS: {
                "heart_rate": rate * 0.8,
                "systolic_bp": -rate * 0.6,
                "temperature": rate * 0.03,
                "respiratory_rate": rate * 0.4,
                "spo2": -rate * 0.1,
                "diastolic_bp": -rate * 0.3,
            },
            DeteriorationPattern.RESPIRATORY: {
                "spo2": -rate * 0.5,
                "respiratory_rate": rate * 0.6,
                "heart_rate": rate * 0.4,
                "systolic_bp": -rate * 0.2,
                "temperature": rate * 0.01,
                "diastolic_bp": -rate * 0.1,
            },
            DeteriorationPattern.CARDIAC: {
                "heart_rate": rate * 1.0 * (1 + 0.5 * np.sin(t * 0.3)),
                "systolic_bp": -rate * 0.7,
                "spo2": -rate * 0.3,
                "diastolic_bp": -rate * 0.4,
                "respiratory_rate": rate * 0.2,
                "temperature": 0.0,
            },
        }

        return shifts.get(patient.deterioration, {}).get(param, 0.0)

    def stream(self, rate: float = 1.0) -> Generator[dict, None, None]:
        """Yield vitals readings for all patients at the given rate (Hz)."""
        interval = 1.0 / rate
        while True:
            for patient in self.patients:
                yield self.generate_reading(patient)
            time.sleep(interval)


@click.command()
@click.option("--patients", default=20, help="Number of simulated patients")
@click.option("--rate", default=1.0, help="Readings per second per patient")
@click.option("--duration", default=0, help="Duration in seconds (0=infinite)")
@click.option("--seed", default=None, type=int, help="Random seed")
@click.option("--output", default="kafka", type=click.Choice(["kafka", "stdout"]))
def main(patients: int, rate: float, duration: int, seed: int | None, output: str):
    """Run the patient vitals simulator."""
    logging.basicConfig(level=logging.INFO)
    logger.info(f"Starting simulator: {patients} patients, {rate} Hz")

    simulator = VitalsSimulator(num_patients=patients, seed=seed)
    start = time.time()

    if output == "kafka":
        from .kafka_producer import VitalsProducer
        producer = VitalsProducer()
        for reading in simulator.stream(rate=rate):
            producer.send(reading)
            if duration > 0 and (time.time() - start) >= duration:
                break
        producer.flush()
    else:
        for reading in simulator.stream(rate=rate):
            print(json.dumps(reading))
            if duration > 0 and (time.time() - start) >= duration:
                break


if __name__ == "__main__":
    main()
