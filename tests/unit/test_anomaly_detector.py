"""Unit tests for the statistical anomaly detector.

Covers Shewhart 3-sigma detection, rolling window behavior, monotonic
trend detection, per-patient state isolation, and the configured window
size actually being honored.
"""

import random

from src.processing.anomaly_detector import (
    PatientStats,
    VitalsAnomalyDetector,
)


def _reading(hr: float, **kwargs) -> dict:
    base = {"heart_rate": hr, "timestamp": "2026-01-01T00:00:00Z"}
    base.update(kwargs)
    return base


def _feed_stable(detector, patient_id, n=20, hr=80.0, jitter=0.5, seed=1):
    """Feed n stable readings with small alternating jitter (no trends)."""
    rng = random.Random(seed)
    for i in range(n):
        wobble = jitter if i % 2 == 0 else -jitter
        detector.update(patient_id, _reading(hr + wobble + rng.uniform(-0.1, 0.1)))


class TestShewhartRule:
    def test_no_anomaly_for_stable_signal(self):
        d = VitalsAnomalyDetector()
        anomalies = []
        rng = random.Random(7)
        for i in range(30):
            wobble = 0.5 if i % 2 == 0 else -0.5
            anomalies += d.update("PT-1", _reading(80 + wobble + rng.uniform(-0.1, 0.1)))
        shewhart = [a for a in anomalies if a.rule_violated == "shewhart"]
        assert shewhart == []

    def test_spike_detected_after_baseline(self):
        d = VitalsAnomalyDetector()
        _feed_stable(d, "PT-1", n=20)
        anomalies = d.update("PT-1", _reading(140.0))
        shewhart = [a for a in anomalies if a.rule_violated == "shewhart"]
        assert len(shewhart) == 1
        assert shewhart[0].parameter == "heart_rate"
        assert shewhart[0].z_score > 3.0

    def test_no_shewhart_before_min_baseline(self):
        d = VitalsAnomalyDetector()
        _feed_stable(d, "PT-1", n=5)
        anomalies = d.update("PT-1", _reading(200.0))
        shewhart = [a for a in anomalies if a.rule_violated == "shewhart"]
        assert shewhart == []

    def test_negative_spike_detected(self):
        d = VitalsAnomalyDetector()
        _feed_stable(d, "PT-1", n=20)
        anomalies = d.update("PT-1", _reading(30.0))
        shewhart = [a for a in anomalies if a.rule_violated == "shewhart"]
        assert len(shewhart) == 1
        assert shewhart[0].z_score < -3.0


class TestTrendDetection:
    def test_rising_trend_flagged(self):
        d = VitalsAnomalyDetector(trend_length=5)
        anomalies = []
        for hr in [80, 82, 84, 86, 88, 90]:
            anomalies = d.update("PT-1", _reading(float(hr)))
        trends = [a for a in anomalies if a.rule_violated == "trend"]
        assert len(trends) == 1
        assert "rising" in trends[0].message

    def test_falling_trend_flagged(self):
        d = VitalsAnomalyDetector(trend_length=5)
        anomalies = []
        for spo2 in [98, 97, 96, 95, 94, 93]:
            anomalies = d.update(
                "PT-1", {"spo2": float(spo2), "timestamp": "t"}
            )
        trends = [a for a in anomalies if a.rule_violated == "trend"]
        assert len(trends) == 1
        assert "falling" in trends[0].message

    def test_sustained_run_fires_once_then_rearms(self):
        """A long ramp is one event, then needs 5 fresh moves to fire again."""
        d = VitalsAnomalyDetector(trend_length=5)
        fired = []
        for i, hr in enumerate(range(80, 80 + 2 * 14, 2)):
            for a in d.update("PT-1", _reading(float(hr))):
                if a.rule_violated == "trend":
                    fired.append(i)
        # 14 monotonic readings: fires at the 5th move and the 10th, not 9x
        assert fired == [5, 10]

    def test_non_monotonic_not_flagged(self):
        d = VitalsAnomalyDetector(trend_length=5)
        anomalies = []
        for hr in [80, 82, 81, 83, 82, 84, 83]:
            anomalies += d.update("PT-1", _reading(float(hr)))
        trends = [a for a in anomalies if a.rule_violated == "trend"]
        assert trends == []


class TestPatientIsolation:
    def test_patients_have_independent_baselines(self):
        d = VitalsAnomalyDetector()
        _feed_stable(d, "PT-A", n=20, hr=60.0)
        _feed_stable(d, "PT-B", n=20, hr=110.0, seed=2)

        # 110 is a spike for PT-A but normal for PT-B
        spike_for_a = d.update("PT-A", _reading(110.0))
        normal_for_b = d.update("PT-B", _reading(110.0))

        assert any(a.rule_violated == "shewhart" for a in spike_for_a)
        assert not any(a.rule_violated == "shewhart" for a in normal_for_b)

    def test_monitored_patients_listed(self):
        d = VitalsAnomalyDetector()
        d.update("PT-A", _reading(80))
        d.update("PT-B", _reading(80))
        assert sorted(d.monitored_patients) == ["PT-A", "PT-B"]


class TestPatientStats:
    def test_window_size_is_honored(self):
        stats = PatientStats(window_size=5)
        for v in range(10):
            stats.update("heart_rate", float(v))
        # only the last 5 values should remain in the window
        assert stats.count("heart_rate") == 5
        assert list(stats.values["heart_rate"]) == [5.0, 6.0, 7.0, 8.0, 9.0]

    def test_z_score_zero_when_no_variance(self):
        stats = PatientStats()
        for _ in range(5):
            stats.update("heart_rate", 80.0)
        assert stats.z_score("heart_rate", 80.0) == 0.0

    def test_missing_values_skipped(self):
        d = VitalsAnomalyDetector()
        anomalies = d.update("PT-1", {"heart_rate": None, "timestamp": "t"})
        assert anomalies == []
        assert d.get_patient_stats("PT-1").count("heart_rate") == 0
