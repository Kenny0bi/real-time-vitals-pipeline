"""Shared design system for the dashboard.

The visual language is a hospital central monitoring station: near-black
ground, one phosphor color per physiological channel (the convention real
patient monitors use), monospace type, and severity colors reserved for
MEWS state. The browser demo in docs/index.html uses the same tokens.
"""

from __future__ import annotations

import plotly.graph_objects as go

# Ground and chrome
GROUND = "#05090d"
PANEL = "#0b1117"
PANEL_2 = "#0e161e"
EDGE = "#182430"
GRID = "rgba(110,150,185,0.10)"
TEXT = "#b9cddb"
DIM = "#58718a"
FAINT = "#35485c"

# Channel colors (bedside monitor conventions)
CH = {
    "heart_rate": "#2ef28f",
    "spo2": "#29d8f6",
    "systolic_bp": "#ff6b57",
    "diastolic_bp": "#ffb14d",
    "respiratory_rate": "#f5e04e",
    "temperature": "#d98cf5",
}

# Severity
OK = "#2ef28f"
WARN = "#ffb300"
CRIT = "#ff2f54"

CHANNEL_LABELS = {
    "heart_rate": "HR",
    "spo2": "SpO2",
    "systolic_bp": "SBP",
    "diastolic_bp": "DBP",
    "respiratory_rate": "RR",
    "temperature": "T",
}


def severity_color(mews_total: int) -> str:
    if mews_total >= 5:
        return CRIT
    if mews_total >= 3:
        return WARN
    return OK


def base_layout(**overrides) -> dict:
    """Plotly layout defaults matching the monitor aesthetic."""
    layout = {
        "paper_bgcolor": PANEL,
        "plot_bgcolor": PANEL_2,
        "font": {"family": "IBM Plex Mono, Menlo, monospace",
                 "color": TEXT, "size": 11},
        "margin": {"l": 44, "r": 12, "t": 30, "b": 30},
        "xaxis": {"gridcolor": GRID, "zeroline": False, "linecolor": EDGE},
        "yaxis": {"gridcolor": GRID, "zeroline": False, "linecolor": EDGE},
        "hoverlabel": {"bgcolor": PANEL, "bordercolor": EDGE,
                       "font": {"family": "IBM Plex Mono, monospace", "size": 11}},
        "legend": {"orientation": "h", "y": 1.12, "font": {"size": 10}},
        # multi-series charts stay inside the design system's family
        "colorway": ["#2ef28f", "#29d8f6", "#ff6b57", "#f5e04e", "#d98cf5",
                     "#ffb14d", "#7ea4ff", "#ff8fc6", "#9be8b7", "#c9d6ff"],
    }
    layout.update(overrides)
    return layout


def styled_figure(**layout_overrides) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**base_layout(**layout_overrides))
    return fig


# Spokes clockwise from 12 o'clock: HR, SBP, RR, Temp, AVPU
_ROSE_SPOKES = [
    ("hr", CH["heart_rate"]),
    ("sbp", CH["systolic_bp"]),
    ("rr", CH["respiratory_rate"]),
    ("temp", CH["temperature"]),
    ("avpu", "#9fb6c9"),
]


def mews_rose_svg(scores: dict, size: int = 44) -> str:
    """The MEWS rose glyph: five spokes, one notch per point (0-3).

    ``scores`` needs keys hr, sbp, rr, temp, avpu, total. A well patient is
    a bare center; a full bloom is a MEWS of 15. Same encoding as the
    browser demo, so the glyph reads identically everywhere.
    """
    import math

    c = 32.0
    step, r0 = 6.5, 4.5
    total = scores.get("total", 0)
    center = severity_color(total) if total >= 3 else FAINT
    parts = [
        f'<circle cx="{c}" cy="{c}" r="2.2" fill="{center}"/>'
    ]
    for i, (key, color) in enumerate(_ROSE_SPOKES):
        ang = -math.pi / 2 + i * (2 * math.pi / 5)
        for notch in range(1, 4):
            r1 = r0 + (notch - 1) * step
            r2 = r1 + step - 1.8
            filled = scores.get(key, 0) >= notch
            x1, y1 = c + math.cos(ang) * r1, c + math.sin(ang) * r1
            x2, y2 = c + math.cos(ang) * r2, c + math.sin(ang) * r2
            stroke = color if filled else "rgba(100,135,168,.3)"
            width = 3 if filled else 1.3
            parts.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{stroke}" stroke-width="{width}" stroke-linecap="round"/>'
            )
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 64 64" '
        f'xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>'
    )


def sparkline_svg(values: list[float], color: str,
                  width: int = 150, height: int = 34) -> str:
    """Inline SVG sparkline for the bed-grid tiles (no plotting library)."""
    if len(values) < 2:
        return f'<svg width="{width}" height="{height}"></svg>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    pts = " ".join(
        f"{i * width / (n - 1):.1f},"
        f"{height - 4 - (v - lo) / span * (height - 8):.1f}"
        for i, v in enumerate(values)
    )
    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" '
        f'stroke-width="1.4" stroke-linejoin="round"/></svg>'
    )


PAGE_CSS = f"""
<style>
html, body, [data-testid="stAppViewContainer"] {{
  background: {GROUND};
}}
h1, h2, h3 {{ letter-spacing: .08em; }}
[data-testid="stMetricValue"] {{ font-family: "IBM Plex Mono", monospace; }}
.tile {{
  background: {PANEL}; border: 1px solid {EDGE};
  border-left: 3px solid {OK}; border-radius: 3px;
  padding: 8px 10px; margin-bottom: 10px;
  font-family: "IBM Plex Mono", Menlo, monospace;
}}
.tile.warn {{ border-left-color: {WARN}; }}
.tile.crit {{ border-left-color: {CRIT};
  box-shadow: inset 0 0 0 1px rgba(255,47,84,.35); }}
.tile .row1 {{ display:flex; align-items:baseline; gap:8px; }}
.tile .pid {{ color:#e6f1f9; font-weight:600; font-size:13px; }}
.tile .unit {{ color:{FAINT}; font-size:10px; }}
.tile .mews {{ margin-left:auto; font-size:11px; color:{DIM}; }}
.tile .mews b {{ font-size:16px; }}
.tile .row2 {{ display:flex; align-items:center; gap:10px; margin-top:4px; }}
.tile .nums {{ display:grid; grid-template-columns:repeat(3,1fr);
  gap:1px 10px; font-size:10px; color:{FAINT}; flex:1; }}
.tile .nums span {{ font-size:12.5px; font-weight:500; display:block; }}
.evt {{
  border-left: 2px solid {WARN}; background: {PANEL};
  padding: 6px 10px; font-size: 12px; margin-bottom: 6px;
  color: {DIM}; border-radius: 0 2px 2px 0;
  font-family: "IBM Plex Mono", Menlo, monospace;
}}
.evt b {{ color: {TEXT}; }}
.evt.crit {{ border-left-color: {CRIT}; }}
.evt.crit b {{ color: {CRIT}; }}
.evt time {{ color: {FAINT}; float: right; }}
</style>
"""
