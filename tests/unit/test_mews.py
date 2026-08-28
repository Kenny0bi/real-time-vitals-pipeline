"""Unit tests for the MEWS calculator.

Tests cover every scoring boundary in the MEWS table, total score
calculations for clinical deterioration scenarios, and edge cases.
"""

from src.processing.mews_calculator import (
    MEWSResult,
    calculate_mews,
    calculate_mews_from_dict,
)


class TestHeartRateScoring:
    def test_normal_hr(self):
        result = calculate_mews(heart_rate=75)
        assert result.hr_score == 0

    def test_hr_boundary_51(self):
        assert calculate_mews(heart_rate=51).hr_score == 0

    def test_hr_boundary_50(self):
        assert calculate_mews(heart_rate=50).hr_score == 1

    def test_hr_mild_tachycardia(self):
        assert calculate_mews(heart_rate=105).hr_score == 1

    def test_hr_moderate_tachycardia(self):
        assert calculate_mews(heart_rate=120).hr_score == 2

    def test_hr_severe_tachycardia(self):
        assert calculate_mews(heart_rate=135).hr_score == 3

    def test_hr_boundary_130(self):
        assert calculate_mews(heart_rate=130).hr_score == 3

    def test_hr_bradycardia(self):
        assert calculate_mews(heart_rate=35).hr_score == 2

    def test_hr_mild_bradycardia(self):
        assert calculate_mews(heart_rate=45).hr_score == 1


class TestSystolicBPScoring:
    def test_normal_bp(self):
        assert calculate_mews(systolic_bp=120).sbp_score == 0

    def test_bp_low_normal(self):
        assert calculate_mews(systolic_bp=101).sbp_score == 0

    def test_bp_mild_hypotension(self):
        assert calculate_mews(systolic_bp=90).sbp_score == 1

    def test_bp_moderate_hypotension(self):
        assert calculate_mews(systolic_bp=75).sbp_score == 2

    def test_bp_severe_hypotension(self):
        assert calculate_mews(systolic_bp=65).sbp_score == 3

    def test_bp_hypertension(self):
        assert calculate_mews(systolic_bp=210).sbp_score == 2


class TestRespiratoryRateScoring:
    def test_normal_rr(self):
        assert calculate_mews(respiratory_rate=14).rr_score == 0

    def test_rr_mildly_elevated(self):
        assert calculate_mews(respiratory_rate=18).rr_score == 1

    def test_rr_elevated(self):
        assert calculate_mews(respiratory_rate=25).rr_score == 2

    def test_rr_severe(self):
        assert calculate_mews(respiratory_rate=32).rr_score == 3

    def test_rr_low(self):
        assert calculate_mews(respiratory_rate=7).rr_score == 2


class TestTemperatureScoring:
    def test_normal_temp(self):
        assert calculate_mews(temperature=37.0).temp_score == 0

    def test_fever(self):
        assert calculate_mews(temperature=39.0).temp_score == 2

    def test_hypothermia(self):
        assert calculate_mews(temperature=34.5).temp_score == 2

    def test_boundary_38_5(self):
        assert calculate_mews(temperature=38.5).temp_score == 2

    def test_boundary_35_0(self):
        assert calculate_mews(temperature=35.0).temp_score == 0


class TestAVPUScoring:
    def test_alert(self):
        assert calculate_mews(avpu="A").avpu_score == 0

    def test_voice(self):
        assert calculate_mews(avpu="V").avpu_score == 1

    def test_pain(self):
        assert calculate_mews(avpu="P").avpu_score == 2

    def test_unresponsive(self):
        assert calculate_mews(avpu="U").avpu_score == 3

    def test_lowercase(self):
        assert calculate_mews(avpu="v").avpu_score == 1


class TestTotalMEWS:
    def test_all_normal(self):
        result = calculate_mews(
            heart_rate=75, systolic_bp=120, respiratory_rate=14,
            temperature=37.0, avpu="A",
        )
        assert result.total == 0
        assert result.severity == "normal"

    def test_sepsis_scenario(self):
        """Septic patient: tachycardic, hypotensive, febrile, tachypneic."""
        result = calculate_mews(
            heart_rate=125, systolic_bp=85, respiratory_rate=28,
            temperature=39.5, avpu="V",
        )
        assert result.total >= 5
        assert result.severity == "critical"

    def test_respiratory_failure(self):
        result = calculate_mews(
            heart_rate=115, systolic_bp=100, respiratory_rate=32,
            temperature=36.5, avpu="V",
        )
        assert result.total >= 5

    def test_cardiac_arrest_scenario(self):
        result = calculate_mews(
            heart_rate=140, systolic_bp=60, respiratory_rate=35,
            temperature=36.0, avpu="U",
        )
        assert result.total >= 10

    def test_warning_level(self):
        result = calculate_mews(
            heart_rate=105, systolic_bp=120, respiratory_rate=16,
            temperature=37.0, avpu="A",
        )
        assert result.total >= 1
        assert result.severity in ("normal", "warning")


class TestEdgeCases:
    def test_all_none(self):
        result = calculate_mews()
        assert result.total == 0

    def test_missing_hr(self):
        result = calculate_mews(systolic_bp=120, respiratory_rate=14)
        assert result.hr_score == 0

    def test_from_dict(self):
        vitals = {
            "heart_rate": 130,
            "systolic_bp": 65,
            "respiratory_rate": 32,
            "temperature": 39.0,
            "avpu": "P",
        }
        result = calculate_mews_from_dict(vitals)
        assert result.total >= 10

    def test_mews_result_severity_property(self):
        r1 = MEWSResult(total=2, hr_score=1, sbp_score=1, rr_score=0, temp_score=0, avpu_score=0)
        assert r1.severity == "normal"
        r2 = MEWSResult(total=4, hr_score=2, sbp_score=1, rr_score=1, temp_score=0, avpu_score=0)
        assert r2.severity == "warning"
        r3 = MEWSResult(total=7, hr_score=3, sbp_score=2, rr_score=2, temp_score=0, avpu_score=0)
        assert r3.severity == "critical"
