"""Unit tests for the vitals simulator.

Verifies reading structure, physiological plausibility, deterministic
seeding, and that deterioration trajectories actually deteriorate in the
clinically expected direction.
"""

from src.ingestion.vitals_simulator import (
    BASELINE_RANGES,
    DeteriorationPattern,
    PatientProfile,
    VitalsSimulator,
)

REQUIRED_KEYS = {
    "patient_id", "timestamp", "heart_rate", "systolic_bp", "diastolic_bp",
    "respiratory_rate", "spo2", "temperature", "avpu", "device_id", "unit",
}


class TestReadingStructure:
    def test_reading_has_all_fields(self):
        sim = VitalsSimulator(num_patients=1, seed=42)
        reading = sim.generate_reading(sim.patients[0])
        assert set(reading.keys()) == REQUIRED_KEYS

    def test_values_within_physiological_limits(self):
        sim = VitalsSimulator(num_patients=10, seed=42)
        for patient in sim.patients:
            for _ in range(50):
                r = sim.generate_reading(patient)
                assert 20 <= r["heart_rate"] <= 300
                assert 30 <= r["systolic_bp"] <= 300
                assert 4 <= r["respiratory_rate"] <= 60
                assert 50 <= r["spo2"] <= 100
                assert 30 <= r["temperature"] <= 45
                assert r["avpu"] in ("A", "V", "P", "U")

    def test_seed_reproducibility(self):
        sim1 = VitalsSimulator(num_patients=3, seed=7)
        sim2 = VitalsSimulator(num_patients=3, seed=7)
        for p1, p2 in zip(sim1.patients, sim2.patients, strict=True):
            assert p1.baselines == p2.baselines
            assert p1.deterioration == p2.deterioration


class TestBaselines:
    def test_baselines_within_healthy_ranges(self):
        sim = VitalsSimulator(num_patients=20, seed=42)
        for patient in sim.patients:
            for param, (lo, hi) in BASELINE_RANGES.items():
                assert lo <= patient.baselines[param] <= hi

    def test_deterioration_prob_zero_gives_no_deteriorating_patients(self):
        sim = VitalsSimulator(num_patients=50, deterioration_prob=0.0, seed=42)
        assert all(
            p.deterioration == DeteriorationPattern.NONE for p in sim.patients
        )

    def test_deterioration_prob_one_gives_all_deteriorating(self):
        sim = VitalsSimulator(num_patients=50, deterioration_prob=1.0, seed=42)
        assert all(
            p.deterioration != DeteriorationPattern.NONE for p in sim.patients
        )


class TestDeteriorationTrajectories:
    def _make_patient(self, pattern: DeteriorationPattern) -> PatientProfile:
        return PatientProfile(
            patient_id="PT-TEST",
            unit="ICU-2A",
            device_id="MON-TEST",
            deterioration=pattern,
            deterioration_rate=0.05,
        )

    def _run(self, sim, patient, n=300):
        first = sim.generate_reading(patient)
        last = first
        for _ in range(n - 1):
            last = sim.generate_reading(patient)
        return first, last

    def test_sepsis_trajectory(self):
        """Sepsis: HR up, BP down, temperature up, RR up."""
        sim = VitalsSimulator(num_patients=0, seed=42)
        patient = self._make_patient(DeteriorationPattern.SEPSIS)
        first, last = self._run(sim, patient)
        assert last["heart_rate"] > first["heart_rate"]
        assert last["systolic_bp"] < first["systolic_bp"]
        assert last["temperature"] > first["temperature"]
        assert last["respiratory_rate"] > first["respiratory_rate"]

    def test_respiratory_trajectory(self):
        """Respiratory failure: SpO2 down, RR up."""
        sim = VitalsSimulator(num_patients=0, seed=42)
        patient = self._make_patient(DeteriorationPattern.RESPIRATORY)
        first, last = self._run(sim, patient)
        assert last["spo2"] < first["spo2"]
        assert last["respiratory_rate"] > first["respiratory_rate"]

    def test_cardiac_trajectory(self):
        """Cardiac event: BP down, SpO2 down."""
        sim = VitalsSimulator(num_patients=0, seed=42)
        patient = self._make_patient(DeteriorationPattern.CARDIAC)
        first, last = self._run(sim, patient)
        assert last["systolic_bp"] < first["systolic_bp"]
        assert last["spo2"] < first["spo2"]

    def test_stable_patient_stays_near_baseline(self):
        sim = VitalsSimulator(num_patients=0, seed=42)
        patient = PatientProfile(
            patient_id="PT-STABLE", unit="ICU-2A", device_id="MON-S",
        )
        readings = [sim.generate_reading(patient) for _ in range(200)]
        hr_values = [r["heart_rate"] for r in readings]
        drift = abs(hr_values[-1] - hr_values[0])
        # stable patients only wobble with noise (sd 3), never trend away
        assert drift < 20
