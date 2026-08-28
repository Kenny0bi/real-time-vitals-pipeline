"""Unit tests for the transport-free stream processing core.

VitalsProcessor is the heart of the no-Spark path: these tests prove the
full score-detect-decide flow works without Kafka, Postgres, or Redis.
"""

from src.config.settings import settings
from src.processing.stream_worker import ProcessedReading, VitalsProcessor


def _reading(**overrides) -> dict:
    base = {
        "patient_id": "PT-00001",
        "timestamp": "2026-01-01T00:00:00Z",
        "heart_rate": 80.0,
        "systolic_bp": 120.0,
        "diastolic_bp": 75.0,
        "respiratory_rate": 15.0,
        "spo2": 98.0,
        "temperature": 37.0,
        "avpu": "A",
        "device_id": "MON-1",
        "unit": "ICU-2A",
    }
    base.update(overrides)
    return base


class TestProcessing:
    def test_normal_reading_scored_no_alerts(self):
        p = VitalsProcessor()
        result = p.process(_reading(respiratory_rate=12.0))
        assert result.mews.total == 0
        assert result.alerts == []
        assert result.scored_record["mews_score"] == 0
        assert result.scored_record["severity"] == "normal"

    def test_critical_reading_generates_mews_alert(self):
        p = VitalsProcessor()
        result = p.process(_reading(
            heart_rate=135, systolic_bp=75, respiratory_rate=32,
            temperature=39.0, avpu="P",
        ))
        # 3 + 2 + 3 + 2 + 2 = 12
        assert result.mews.total == 12
        mews_alerts = [
            a for a in result.alerts if a["alert_type"] == "mews_threshold"
        ]
        assert len(mews_alerts) == 1
        assert mews_alerts[0]["severity"] == "critical"
        assert mews_alerts[0]["mews_score"] == 12

    def test_threshold_boundary(self):
        p = VitalsProcessor()
        # exactly at the alert threshold fires; one below does not
        at = p.process(_reading(heart_rate=135, systolic_bp=90,
                                respiratory_rate=18, avpu="V"))
        assert at.mews.total == settings.mews_alert_threshold + 1
        below = VitalsProcessor().process(
            _reading(heart_rate=115, respiratory_rate=25)
        )
        assert below.mews.total == 4
        assert below.alerts == []

    def test_scored_record_contains_breakdown(self):
        result = VitalsProcessor().process(_reading(heart_rate=115))
        record = result.scored_record
        for key in ("mews_hr", "mews_sbp", "mews_rr", "mews_temp",
                    "mews_avpu", "mews_score", "severity"):
            assert key in record
        assert record["mews_hr"] == 2

    def test_anomaly_becomes_warning_alert(self):
        p = VitalsProcessor()
        for i in range(20):
            wobble = 0.5 if i % 2 == 0 else -0.5
            p.process(_reading(heart_rate=80.0 + wobble,
                               respiratory_rate=12.0))
        result = p.process(_reading(heart_rate=95.0, respiratory_rate=12.0))
        anomaly_alerts = [
            a for a in result.alerts if a["alert_type"].startswith("anomaly_")
        ]
        assert len(anomaly_alerts) >= 1
        assert all(a["severity"] == "warning" for a in anomaly_alerts)

    def test_alert_latch_suppresses_repeats(self):
        """A patient parked above threshold alerts once, not every reading."""
        p = VitalsProcessor()
        critical = dict(heart_rate=135, systolic_bp=75, respiratory_rate=32)
        first = p.process(_reading(**critical))
        assert any(a["alert_type"] == "mews_threshold" for a in first.alerts)
        for _ in range(10):
            again = p.process(_reading(**critical))
            assert not any(
                a["alert_type"] == "mews_threshold" for a in again.alerts
            )

    def test_alert_rearms_after_sustained_recovery(self):
        p = VitalsProcessor()
        critical = dict(heart_rate=135, systolic_bp=75, respiratory_rate=32)
        p.process(_reading(**critical))
        for _ in range(VitalsProcessor.REARM_READINGS):
            p.process(_reading(respiratory_rate=12.0))
        again = p.process(_reading(**critical))
        assert any(a["alert_type"] == "mews_threshold" for a in again.alerts)

    def test_latch_is_per_patient(self):
        p = VitalsProcessor()
        critical = dict(heart_rate=135, systolic_bp=75, respiratory_rate=32)
        p.process(_reading(**critical))  # latches PT-00001
        other = p.process(_reading(patient_id="PT-00002", **critical))
        assert any(a["alert_type"] == "mews_threshold" for a in other.alerts)

    def test_counters(self):
        p = VitalsProcessor()
        for _ in range(5):
            p.process(_reading(respiratory_rate=12.0))
        assert p.processed_count == 5
        assert p.alert_count == 0


class TestProcessedReading:
    def test_alert_message_names_components(self):
        result = VitalsProcessor().process(_reading(
            heart_rate=135, systolic_bp=65, respiratory_rate=35,
        ))
        message = result.alerts[0]["message"]
        assert "MEWS" in message
        assert "HR 3" in message
        assert "SBP 3" in message

    def test_preserves_original_reading(self):
        reading = _reading()
        result = ProcessedReading(
            reading=reading,
            mews=VitalsProcessor().process(reading).mews,
        )
        assert result.scored_record["device_id"] == "MON-1"
        assert result.scored_record["unit"] == "ICU-2A"
