"""Modified Early Warning Score (MEWS) calculator.

Implements the standardized MEWS scoring system used in clinical practice to
identify patients at risk of deterioration. Each vital sign parameter receives
a score from 0 to 3 based on predefined thresholds; the total MEWS (0–15)
determines urgency of clinical response.

Scoring thresholds follow the standard MEWS definition:

    Parameter        | 3     | 2       | 1       | 0         | 1       | 2       | 3
    Systolic BP      | <70   | 71–80   | 81–100  | 101–199   |         | ≥200    |
    Heart Rate       |       | <40     | 41–50   | 51–100    | 101–110 | 111–129 | ≥130
    Respiratory Rate |       | <9      |         | 9–14      | 15–20   | 21–29   | ≥30
    Temperature (°C) |       | <35.0   |         | 35.0–38.4 |         | ≥38.5   |
    AVPU             |       |         |         | Alert     | Voice   | Pain    | Unresponsive

References:
  - Subbe et al. (2001). Validation of a modified Early Warning Score.
    QJM: An International Journal of Medicine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MEWSResult:
    """Result of MEWS calculation with per-parameter breakdown."""
    total: int
    hr_score: int
    sbp_score: int
    rr_score: int
    temp_score: int
    avpu_score: int

    @property
    def severity(self) -> str:
        if self.total >= 5:
            return "critical"
        elif self.total >= 3:
            return "warning"
        else:
            return "normal"


def calculate_mews(
    heart_rate: float | None = None,
    systolic_bp: float | None = None,
    respiratory_rate: float | None = None,
    temperature: float | None = None,
    avpu: str | None = None,
) -> MEWSResult:
    """Calculate the Modified Early Warning Score from vital signs.

    Missing parameters are scored as 0 (normal) — this is a documented
    clinical assumption to avoid false alerts from incomplete readings.

    Parameters
    ----------
    heart_rate : beats per minute
    systolic_bp : mmHg
    respiratory_rate : breaths per minute
    temperature : degrees Celsius
    avpu : one of 'A' (Alert), 'V' (Voice), 'P' (Pain), 'U' (Unresponsive)

    Returns
    -------
    MEWSResult with total score and per-parameter breakdown
    """
    hr = _score_heart_rate(heart_rate)
    sbp = _score_systolic_bp(systolic_bp)
    rr = _score_respiratory_rate(respiratory_rate)
    temp = _score_temperature(temperature)
    avpu_s = _score_avpu(avpu)

    return MEWSResult(
        total=hr + sbp + rr + temp + avpu_s,
        hr_score=hr,
        sbp_score=sbp,
        rr_score=rr,
        temp_score=temp,
        avpu_score=avpu_s,
    )


def _score_heart_rate(hr: float | None) -> int:
    if hr is None:
        return 0
    if hr >= 130:
        return 3
    if hr >= 111:
        return 2
    if hr >= 101:
        return 1
    if hr >= 51:
        return 0
    if hr >= 41:
        return 1
    return 2  # <=40, bradycardia


def _score_systolic_bp(sbp: float | None) -> int:
    if sbp is None:
        return 0
    if sbp >= 200:
        return 2
    if sbp >= 101:
        return 0
    if sbp >= 81:
        return 1
    if sbp >= 71:
        return 2
    return 3  # <70


def _score_respiratory_rate(rr: float | None) -> int:
    if rr is None:
        return 0
    if rr >= 30:
        return 3
    if rr >= 21:
        return 2
    if rr >= 15:
        return 1
    if rr >= 9:
        return 0
    return 2  # <9, bradypnea


def _score_temperature(temp: float | None) -> int:
    if temp is None:
        return 0
    if temp >= 38.5:
        return 2
    if temp >= 35.0:
        return 0
    return 2  # <35.0, hypothermia


def _score_avpu(avpu: str | None) -> int:
    if avpu is None:
        return 0
    avpu = avpu.upper().strip()
    return {"A": 0, "V": 1, "P": 2, "U": 3}.get(avpu, 0)


def calculate_mews_from_dict(vitals: dict) -> MEWSResult:
    """Calculate MEWS from a vitals dictionary (as produced by the simulator)."""
    return calculate_mews(
        heart_rate=vitals.get("heart_rate"),
        systolic_bp=vitals.get("systolic_bp"),
        respiratory_rate=vitals.get("respiratory_rate"),
        temperature=vitals.get("temperature"),
        avpu=vitals.get("avpu"),
    )
