"""Unit tests for the data quality validator."""

from src.quality.vitals_expectations import (
    PHYSIOLOGICAL_RANGES,
    ValidationReport,
    VitalsValidator,
)


def _clean_reading(**overrides) -> dict:
    reading = {
        "patient_id": "PT-00001",
        "timestamp": "2026-01-01T00:00:00Z",
        "heart_rate": 80.0,
        "systolic_bp": 120.0,
        "diastolic_bp": 75.0,
        "respiratory_rate": 15.0,
        "spo2": 98.0,
        "temperature": 37.0,
        "avpu": "A",
    }
    reading.update(overrides)
    return reading


class TestSingleReading:
    def test_clean_reading_passes(self):
        assert VitalsValidator().validate_reading(_clean_reading()) == []

    def test_missing_patient_id_fails(self):
        issues = VitalsValidator().validate_reading(
            _clean_reading(patient_id=None)
        )
        assert any(i.check == "not_null" and i.field == "patient_id"
                   for i in issues)

    def test_missing_timestamp_fails(self):
        issues = VitalsValidator().validate_reading(
            _clean_reading(timestamp=""))
        assert any(i.field == "timestamp" for i in issues)

    def test_impossible_heart_rate_fails(self):
        issues = VitalsValidator().validate_reading(
            _clean_reading(heart_rate=400))
        assert any(i.check == "physiological_range" for i in issues)

    def test_range_boundaries_pass(self):
        v = VitalsValidator()
        for field, (lo, hi) in PHYSIOLOGICAL_RANGES.items():
            assert v.validate_reading(_clean_reading(**{field: lo})) == []
            assert v.validate_reading(_clean_reading(**{field: hi})) == []

    def test_just_outside_boundaries_fail(self):
        v = VitalsValidator()
        for field, (lo, hi) in PHYSIOLOGICAL_RANGES.items():
            assert v.validate_reading(_clean_reading(**{field: lo - 0.1}))
            assert v.validate_reading(_clean_reading(**{field: hi + 0.1}))

    def test_missing_vital_is_allowed(self):
        # partial readings are legal; MEWS scores missing params as 0
        assert VitalsValidator().validate_reading(
            _clean_reading(heart_rate=None)) == []

    def test_non_numeric_vital_fails(self):
        issues = VitalsValidator().validate_reading(
            _clean_reading(heart_rate="high"))
        assert any(i.check == "numeric" for i in issues)

    def test_invalid_avpu_fails(self):
        issues = VitalsValidator().validate_reading(_clean_reading(avpu="X"))
        assert any(i.check == "in_set" for i in issues)

    def test_lowercase_avpu_passes(self):
        assert VitalsValidator().validate_reading(
            _clean_reading(avpu="v")) == []


class TestBatchValidation:
    def test_report_counts(self):
        batch = [
            _clean_reading(),
            _clean_reading(heart_rate=400),
            _clean_reading(patient_id=None, spo2=40),
        ]
        report = VitalsValidator().validate_batch(batch)
        assert report.total == 3
        assert report.passed == 1
        assert report.failed == 2
        assert len(report.issues) == 3  # 1 + 2 issues

    def test_pass_rate(self):
        report = VitalsValidator().validate_batch(
            [_clean_reading() for _ in range(4)]
        )
        assert report.pass_rate == 1.0

    def test_empty_batch(self):
        report = VitalsValidator().validate_batch([])
        assert report.total == 0
        assert report.pass_rate == 1.0

    def test_summary_groups_by_check(self):
        batch = [
            _clean_reading(heart_rate=400),
            _clean_reading(spo2=101),
        ]
        summary = VitalsValidator().validate_batch(batch).summary()
        assert summary["issues_by_check"]["physiological_range"] == 2

    def test_report_dataclass_defaults(self):
        report = ValidationReport()
        assert report.failed == 0
        assert report.pass_rate == 1.0
