"""Streamlit dashboard: an ICU central monitoring station.

Five views: Monitor Wall (bed grid with live sparklines and MEWS roses),
Patient Monitor (waveform trends + MEWS rhythm strip), Alert Timeline,
Trend Analysis, and System Health.

Runs in two modes:
- live: reads TimescaleDB, i.e. what the streaming pipeline actually wrote
- demo: runs the real simulator and stream-processing core in-process,
  so the dashboard works with zero infrastructure (`make demo`)

Design note: the visual language is a hospital telemetry station, not a
BI tool. Dark ground, per-channel phosphor colors, monospace numerals,
and a per-patient "MEWS rose" glyph encoding the five score components.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# `streamlit run src/dashboard/app.py` executes this file as a script, so
# make the project root importable and use absolute imports throughout.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.dashboard import theme  # noqa: E402
from src.dashboard.data import (  # noqa: E402
    DemoData,
    LiveData,
    _build_demo_state,
)

st.set_page_config(
    page_title="Vitals Monitor",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(theme.PAGE_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------- data source
@st.cache_resource
def _demo_state():
    return _build_demo_state()


def get_source():
    if os.environ.get("VITALS_DEMO_MODE") == "1":
        return DemoData(_demo_state()), None
    try:
        return LiveData(), None
    except Exception as exc:  # noqa: BLE001 - fall back with a visible banner
        return DemoData(_demo_state()), (
            f"TimescaleDB is not reachable ({type(exc).__name__}), showing "
            "the in-process demo instead. Run `docker compose up -d` and the "
            "pipeline for live data."
        )


def mews_parts(record: dict) -> dict:
    return {
        "hr": record.get("mews_hr", 0), "sbp": record.get("mews_sbp", 0),
        "rr": record.get("mews_rr", 0), "temp": record.get("mews_temp", 0),
        "avpu": record.get("mews_avpu", 0),
        "total": record.get("mews_score", 0),
    }


def hist_frame(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if not df.empty:
        # readings are stamped UTC; show them in the viewer's local time
        df["timestamp"] = (
            pd.to_datetime(df["timestamp"], utc=True)
            .dt.tz_convert(datetime.now().astimezone().tzinfo)
            .dt.tz_localize(None)
        )
    return df


# ---------------------------------------------------------------- views
def render_wall(source):
    latest = source.patients_latest()
    counts = {"normal": 0, "warning": 0, "critical": 0}
    for r in latest:
        m = r.get("mews_score", 0)
        counts["critical" if m >= 5 else "warning" if m >= 3 else "normal"] += 1

    c1, c2, c3, c4 = st.columns([2.4, 1, 1, 1])
    c1.markdown("### Monitor Wall")
    c2.metric("Stable", counts["normal"])
    c3.metric("Watch", counts["warning"])
    c4.metric("Critical", counts["critical"])

    cols = st.columns(4)
    for i, record in enumerate(latest):
        pid = record["patient_id"]
        m = mews_parts(record)
        sev = "crit" if m["total"] >= 5 else "warn" if m["total"] >= 3 else ""
        hr_hist = [
            r["heart_rate"] for r in source.patient_history(pid, seconds=120)
            if r.get("heart_rate") is not None
        ]
        spark = theme.sparkline_svg(hr_hist[-60:], theme.CH["heart_rate"])
        rose = theme.mews_rose_svg(m, size=44)
        mews_color = theme.severity_color(m["total"])
        sbp = record.get("systolic_bp")
        dbp = record.get("diastolic_bp")
        bp = f"{sbp:.0f}/{dbp:.0f}" if sbp is not None and dbp is not None else "—"

        def num(key, fmt="{:.0f}", rec=record):
            v = rec.get(key)
            return fmt.format(v) if v is not None else "—"

        cols[i % 4].markdown(
            f"""<div class="tile {sev}">
              <div class="row1"><span class="pid">{pid}</span>
                <span class="unit">{record.get("unit", "")}</span>
                <span class="mews">MEWS <b style="color:{mews_color}">{m["total"]}</b></span></div>
              <div class="row2">{spark}{rose}</div>
              <div class="row2"><div class="nums">
                <div>HR<span style="color:{theme.CH["heart_rate"]}">{num("heart_rate")}</span></div>
                <div>SpO2<span style="color:{theme.CH["spo2"]}">{num("spo2")}</span></div>
                <div>BP<span style="color:{theme.CH["systolic_bp"]}">{bp}</span></div>
                <div>RR<span style="color:{theme.CH["respiratory_rate"]}">{num("respiratory_rate")}</span></div>
                <div>T<span style="color:{theme.CH["temperature"]}">{num("temperature", "{:.1f}")}</span></div>
                <div>AVPU<span>{record.get("avpu", "—")}</span></div>
              </div></div>
            </div>""",
            unsafe_allow_html=True,
        )

    st.caption(
        "Tile edge: green stable, amber MEWS 3-4, red MEWS 5+. The glyph is "
        "the MEWS rose: five spokes (HR, SBP, RR, T, consciousness), one "
        "filled notch per point. Sparkline: last minute of heart rate."
    )


def render_patient_monitor(source):
    st.markdown("### Patient Monitor")
    latest = source.patients_latest()
    if not latest:
        st.info("No patient data yet.")
        return
    ids = [r["patient_id"] for r in latest]
    default = next(
        (i for i, r in enumerate(latest) if r.get("mews_score", 0) >= 5), 0
    )
    pid = st.selectbox("Patient", ids, index=default)
    minutes = st.slider("Window (minutes)", 5, 30, 30, step=5)
    df = hist_frame(source.patient_history(pid, seconds=minutes * 60))
    if df.empty:
        st.info("No history for this patient yet.")
        return

    last = df.iloc[-1].to_dict()
    m = mews_parts(last)
    color = theme.severity_color(m["total"])

    c1, c2, c3 = st.columns([1, 1, 2.4])
    c1.markdown(
        f'<div style="font-size:52px;font-weight:600;color:{color};'
        f'font-family:monospace">{m["total"]}'
        f'<span style="font-size:13px;color:{theme.DIM};display:block;'
        f'letter-spacing:.12em">MEWS</span></div>',
        unsafe_allow_html=True,
    )
    c2.markdown(theme.mews_rose_svg(m, size=92), unsafe_allow_html=True)
    breakdown = pd.DataFrame({
        "parameter": ["Heart rate", "Systolic BP", "Resp rate",
                      "Temperature", "Consciousness"],
        "value": [
            f"{last.get('heart_rate', float('nan')):.0f} bpm",
            f"{last.get('systolic_bp', float('nan')):.0f} mmHg",
            f"{last.get('respiratory_rate', float('nan')):.0f} /min",
            f"{last.get('temperature', float('nan')):.1f} C",
            {"A": "Alert", "V": "Voice", "P": "Pain",
             "U": "Unresponsive"}.get(last.get("avpu"), "—"),
        ],
        "score": [f"+{m[k]}" for k in ("hr", "sbp", "rr", "temp", "avpu")],
    })
    c3.dataframe(breakdown, hide_index=True, use_container_width=True)

    # 2x2 waveform grid, one channel per pane in its monitor color
    panes = [
        ("Heart rate (bpm)", [("heart_rate", theme.CH["heart_rate"])]),
        ("Blood pressure (mmHg)", [("systolic_bp", theme.CH["systolic_bp"]),
                                   ("diastolic_bp", theme.CH["diastolic_bp"])]),
        ("SpO2 (%)", [("spo2", theme.CH["spo2"])]),
        ("Respiratory rate (/min)",
         [("respiratory_rate", theme.CH["respiratory_rate"])]),
    ]
    grid = st.columns(2)
    for i, (title, channels) in enumerate(panes):
        fig = theme.styled_figure(title={"text": title, "font": {"size": 12}},
                                  height=230, showlegend=False)
        for key, color_ in channels:
            fig.add_trace(go.Scatter(
                x=df["timestamp"], y=df[key], mode="lines",
                line={"color": color_, "width": 1.4}, name=key,
            ))
        grid[i % 2].plotly_chart(fig, use_container_width=True,
                                 key=f"pane-{pid}-{i}")

    # MEWS rhythm strip with observation/critical bands
    strip = theme.styled_figure(
        title={"text": "MEWS rhythm strip", "font": {"size": 12}},
        height=200, showlegend=False,
    )
    strip.add_hrect(y0=3, y1=5, fillcolor="rgba(255,179,0,.07)", line_width=0)
    strip.add_hrect(y0=5, y1=12, fillcolor="rgba(255,47,84,.08)", line_width=0)
    strip.add_hline(y=3, line={"color": theme.WARN, "dash": "dot", "width": 1})
    strip.add_hline(y=5, line={"color": theme.CRIT, "dash": "dot", "width": 1})
    strip.add_trace(go.Scatter(
        x=df["timestamp"], y=df["mews_score"], mode="lines",
        line={"color": "#dfe9f2", "width": 1.6, "shape": "hv"},
    ))
    strip.update_yaxes(range=[-0.5, max(8, df["mews_score"].max() + 1)])
    st.plotly_chart(strip, use_container_width=True, key=f"strip-{pid}")

    st.markdown("##### Alerts for this patient")
    patient_alerts = [a for a in source.alerts(200)
                      if a.get("patient_id") == pid]
    if not patient_alerts:
        st.caption("No alerts for this patient.")
    for a in patient_alerts[:12]:
        render_alert(a)


def render_alert(a: dict):
    sev = "crit" if a.get("severity") == "critical" else ""
    ts = str(a.get("timestamp", ""))[:19].replace("T", " ")
    st.markdown(
        f'<div class="evt {sev}"><time>{ts}</time>'
        f'<b>{a.get("patient_id", "?")}</b> '
        f'[{a.get("alert_type", "alert")}] {a.get("message", "")}</div>',
        unsafe_allow_html=True,
    )


def render_alert_timeline(source):
    st.markdown("### Alert Timeline")
    severities = st.multiselect(
        "Severity", ["critical", "warning"], default=["critical", "warning"]
    )
    alerts = [a for a in source.alerts(200) if a.get("severity") in severities]
    if not alerts:
        st.caption(
            "No alerts yet. Deteriorating patients need a few minutes to "
            "cross their thresholds."
        )
    for a in alerts[:60]:
        render_alert(a)


def render_trends(source):
    st.markdown("### Trend Analysis")
    latest = source.patients_latest()
    ids = [r["patient_id"] for r in latest]
    if not ids:
        st.info("No data yet.")
        return
    param = st.selectbox("Vital sign", list(theme.CH.keys()),
                         format_func=lambda k: theme.CHANNEL_LABELS[k])
    chosen = st.multiselect("Patients", ids, default=ids[:6])

    overlay = theme.styled_figure(height=320, showlegend=True)
    frames = {}
    for pid in chosen:
        df = hist_frame(source.patient_history(pid, seconds=1800))
        if df.empty:
            continue
        frames[pid] = df
        overlay.add_trace(go.Scatter(
            x=df["timestamp"], y=df[param], mode="lines", name=pid,
            line={"width": 1.2}, opacity=0.85,
        ))
    overlay.update_layout(
        title={"text": f"{theme.CHANNEL_LABELS[param]} — last 30 minutes",
               "font": {"size": 12}})
    st.plotly_chart(overlay, use_container_width=True)

    c1, c2 = st.columns(2)
    hist_fig = theme.styled_figure(
        height=280, showlegend=False,
        title={"text": f"{theme.CHANNEL_LABELS[param]} distribution",
               "font": {"size": 12}})
    all_vals = pd.concat(
        [df[param] for df in frames.values()], ignore_index=True
    ) if frames else pd.Series(dtype=float)
    hist_fig.add_trace(go.Histogram(
        x=all_vals, marker={"color": theme.CH[param]}, opacity=0.85,
        nbinsx=40,
    ))
    c1.plotly_chart(hist_fig, use_container_width=True)

    # MEWS heatmap: patients x time buckets, dark -> amber -> red
    heat_rows, heat_ids = [], []
    for pid in ids:
        df = hist_frame(source.patient_history(pid, seconds=1800))
        if df.empty:
            continue
        bucketed = (
            df.set_index("timestamp")["mews_score"]
              .resample("60s").max().tail(30)
        )
        heat_rows.append(bucketed.values)
        heat_ids.append(pid)
    if heat_rows:
        heat = theme.styled_figure(
            height=280,
            title={"text": "MEWS by patient and minute (max per bucket)",
                   "font": {"size": 12}})
        heat.add_trace(go.Heatmap(
            z=heat_rows, y=heat_ids,
            colorscale=[[0, theme.PANEL_2], [0.35, "#1e5c46"],
                        [0.5, theme.WARN], [1.0, theme.CRIT]],
            zmin=0, zmax=8, showscale=True,
            colorbar={"thickness": 8, "outlinewidth": 0},
        ))
        c2.plotly_chart(heat, use_container_width=True)


def render_system_health(source):
    st.markdown("### System Health")
    stats = source.system_stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Readings processed", f"{stats.get('readings_processed', 0):,}")
    c2.metric("Alerts generated", f"{stats.get('alerts_generated', 0):,}")
    c3.metric("Patients", stats.get("patients", 0))
    if source.source_name == "demo":
        c4.metric("Uptime", f"{stats.get('uptime_sec', 0)} s")
    else:
        c4.metric("DB size", f"{stats.get('db_size_mb', 0)} MB")

    throughput = stats.get("throughput") or []
    if throughput:
        fig = theme.styled_figure(
            height=260, showlegend=False,
            title={"text": "Readings ingested per refresh", "font": {"size": 12}})
        local_tz = datetime.now().astimezone().tzinfo
        fig.add_trace(go.Scatter(
            x=[t.astimezone(local_tz).replace(tzinfo=None)
               for t, _ in throughput],
            y=[n for _, n in throughput],
            mode="lines", line={"color": theme.CH["spo2"], "width": 1.4},
            fill="tozeroy", fillcolor="rgba(41,216,246,.08)",
        ))
        st.plotly_chart(fig, use_container_width=True)

    # MEWS distribution across the ward right now
    latest = source.patients_latest()
    if latest:
        scores = [r.get("mews_score", 0) for r in latest]
        fig = theme.styled_figure(
            height=260, showlegend=False,
            title={"text": "Current MEWS distribution", "font": {"size": 12}})
        fig.add_trace(go.Histogram(
            x=scores, nbinsx=12, marker={"color": theme.OK}, opacity=0.85,
        ))
        fig.update_xaxes(title_text="MEWS", dtick=1)
        st.plotly_chart(fig, use_container_width=True)

    if source.source_name == "demo":
        st.caption(
            "Demo mode: the simulator, MEWS calculator, anomaly detector, "
            "and alert rules from src/ run inside this process. In live "
            "mode this page reports TimescaleDB statistics from the "
            "running pipeline."
        )


# ---------------------------------------------------------------- shell
VIEWS = {
    "Monitor Wall": render_wall,
    "Patient Monitor": render_patient_monitor,
    "Alert Timeline": render_alert_timeline,
    "Trend Analysis": render_trends,
    "System Health": render_system_health,
}


def main():
    source, banner = get_source()

    with st.sidebar:
        st.markdown("## Vitals Monitor")
        st.caption(
            f"source: {source.source_name} · "
            f"{datetime.now().strftime('%H:%M:%S')}"
        )
        page = st.radio("View", list(VIEWS.keys()), label_visibility="collapsed")
        st.divider()
        st.caption(
            "Simulated patients only. MEWS thresholds per Subbe et al. "
            "2001: 3-4 increased observation, 5+ immediate review."
        )

    if banner:
        st.warning(banner)

    @st.fragment(run_every="2s")
    def live_view():
        source.advance()
        VIEWS[page](source)

    live_view()


main()
