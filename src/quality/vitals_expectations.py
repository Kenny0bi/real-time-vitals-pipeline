"""Data quality validation for vitals readings.

Two layers:

1. ``VitalsValidator`` — a dependency-free validator implementing the same
   checks as the Great Expectations suite. It runs everywhere (including CI
   and the stream worker hot path) with zero heavy imports.
2. ``build_ge_suite()`` — builds the equivalent Great Expectations
   ExpectationSuite for teams already running a GE deployment. Import is
   deferred so the rest of the pipeline never pays for it.

The physiological ranges are deliberately wide (they mark *impossible*
values, not abnormal ones — abnormal is MEWS's job). A heart rate of 180 is
a sick patient; a heart rate of 400 is a broken sensor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# (min, max) bounds for physically plausible sensor values
PHYSIOLOGICAL_RANGES: dict[str, tuple[float, float]] = {
    "heart_rate": (20, 300),
    "systolic_bp": (30, 300),
    "diastolic_bp": (10, 200),
    "respiratory_rate": (4, 60),
    "spo2": (50, 100),
    "temperature": (30, 45),
}

REQUIRED_FIELDS = ("patient_id", "timestamp")
VALID_AVPU = {"A", "V", "P", "U"}


@dataclass
class ValidationIssue:
    """A single failed check on a single reading."""

    check: str
    field: str
    value: object
    message: str


@dataclass
class ValidationReport:
    """Result of validating a batch of readings."""

    total: int = 0
    passed: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 1.0

    def summary(self) -> dict:
        by_check: dict[str, int] = {}
        for issue in self.issues:
            by_check[issue.check] = by_check.get(issue.check, 0) + 1
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(self.pass_rate, 4),
            "issues_by_check": by_check,
        }


class VitalsValidator:
    """Validate vitals readings against the data quality gates."""

    def validate_reading(self, reading: dict) -> list[ValidationIssue]:
        """Return all issues for one reading (empty list = clean)."""
        issues: list[ValidationIssue] = []

        for f in REQUIRED_FIELDS:
            if not reading.get(f):
                issues.append(ValidationIssue(
                    check="not_null", field=f, value=reading.get(f),
                    message=f"required field '{f}' is missing or empty",
                ))

        for f, (lo, hi) in PHYSIOLOGICAL_RANGES.items():
            value = reading.get(f)
            if value is None:
                continue  # missing vitals are allowed (partial readings)
            try:
                v = float(value)
            except (TypeError, ValueError):
                issues.append(ValidationIssue(
                    check="numeric", field=f, value=value,
                    message=f"'{f}' is not numeric: {value!r}",
                ))
                continue
            if not (lo <= v <= hi):
                issues.append(ValidationIssue(
                    check="physiological_range", field=f, value=v,
                    message=f"'{f}'={v} outside plausible range [{lo}, {hi}]",
                ))

        avpu = reading.get("avpu")
        if avpu is not None and str(avpu).upper().strip() not in VALID_AVPU:
            issues.append(ValidationIssue(
                check="in_set", field="avpu", value=avpu,
                message=f"avpu={avpu!r} not one of {sorted(VALID_AVPU)}",
            ))

        return issues

    def validate_batch(self, readings: list[dict]) -> ValidationReport:
        """Validate a batch and produce a summary report."""
        report = ValidationReport(total=len(readings))
        for reading in readings:
            issues = self.validate_reading(reading)
            if issues:
                report.issues.extend(issues)
            else:
                report.passed += 1
        return report


def build_ge_suite(suite_name: str = "vitals_readings"):
    """Build the equivalent Great Expectations suite (optional dependency).

    Returns an ExpectationSuite mirroring VitalsValidator's checks, for
    deployments that run GE checkpoints against TimescaleDB batches.
    """
    from great_expectations.core.expectation_configuration import (
        ExpectationConfiguration,
    )
    from great_expectations.core.expectation_suite import ExpectationSuite

    expectations = [
        ExpectationConfiguration(
            expectation_type="expect_column_values_to_not_be_null",
            kwargs={"column": col},
        )
        for col in REQUIRED_FIELDS
    ]
    expectations += [
        ExpectationConfiguration(
            expectation_type="expect_column_values_to_be_between",
            kwargs={"column": col, "min_value": lo, "max_value": hi},
        )
        for col, (lo, hi) in PHYSIOLOGICAL_RANGES.items()
    ]
    expectations.append(ExpectationConfiguration(
        expectation_type="expect_column_values_to_be_in_set",
        kwargs={"column": "avpu", "value_set": sorted(VALID_AVPU)},
    ))

    return ExpectationSuite(
        expectation_suite_name=suite_name, expectations=expectations
    )
