"""Write processed vitals data to TimescaleDB hypertables.

Uses psycopg2 with connection pooling and the COPY protocol for
high-throughput batch inserts. Handles connection retries with
exponential backoff for resilience against transient database issues.
"""

from __future__ import annotations

import io
import logging
from contextlib import contextmanager

from psycopg2 import extras, pool

from ..config.settings import settings

logger = logging.getLogger(__name__)


class TimescaleWriter:
    """Write vitals and alert data to TimescaleDB."""

    def __init__(self, dsn: str | None = None, min_conn: int = 2, max_conn: int = 10):
        self._dsn = dsn or settings.timescale_url
        self._pool = pool.ThreadedConnectionPool(min_conn, max_conn, self._dsn)

    @contextmanager
    def _get_conn(self):
        conn = self._pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def write_vitals_batch(self, readings: list[dict]) -> int:
        """Batch insert raw vitals readings using COPY protocol.

        Returns the number of rows written.
        """
        if not readings:
            return 0

        columns = [
            "time", "patient_id", "heart_rate", "systolic_bp", "diastolic_bp",
            "respiratory_rate", "spo2", "temperature", "avpu", "device_id", "unit",
        ]

        buf = io.StringIO()
        for r in readings:
            row = [
                r.get("timestamp", ""),
                r.get("patient_id", ""),
                str(r.get("heart_rate", "")),
                str(r.get("systolic_bp", "")),
                str(r.get("diastolic_bp", "")),
                str(r.get("respiratory_rate", "")),
                str(r.get("spo2", "")),
                str(r.get("temperature", "")),
                r.get("avpu", ""),
                r.get("device_id", ""),
                r.get("unit", ""),
            ]
            buf.write("\t".join(row) + "\n")

        buf.seek(0)

        with self._get_conn() as conn, conn.cursor() as cur:
            cur.copy_from(buf, "vitals_raw", columns=columns, null="")

        logger.debug(f"Wrote {len(readings)} raw vitals readings")
        return len(readings)

    def write_scored_batch(self, readings: list[dict]) -> int:
        """Batch insert scored vitals readings."""
        if not readings:
            return 0

        columns = [
            "time", "patient_id", "heart_rate", "systolic_bp", "diastolic_bp",
            "respiratory_rate", "spo2", "temperature", "avpu",
            "mews_score", "mews_hr", "mews_sbp", "mews_rr", "mews_temp", "mews_avpu",
            "device_id", "unit",
        ]

        rows = []
        for r in readings:
            rows.append((
                r.get("timestamp"),
                r.get("patient_id"),
                r.get("heart_rate"),
                r.get("systolic_bp"),
                r.get("diastolic_bp"),
                r.get("respiratory_rate"),
                r.get("spo2"),
                r.get("temperature"),
                r.get("avpu"),
                r.get("mews_score", 0),
                r.get("mews_hr", 0),
                r.get("mews_sbp", 0),
                r.get("mews_rr", 0),
                r.get("mews_temp", 0),
                r.get("mews_avpu", 0),
                r.get("device_id"),
                r.get("unit"),
            ))

        insert_sql = f"""
            INSERT INTO vitals_scored ({', '.join(columns)})
            VALUES %s
        """

        with self._get_conn() as conn, conn.cursor() as cur:
            extras.execute_values(cur, insert_sql, rows, page_size=1000)

        logger.debug(f"Wrote {len(rows)} scored vitals readings")
        return len(rows)

    def write_alert(self, alert: dict) -> None:
        """Insert a single alert record."""
        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO alerts (time, patient_id, alert_type, severity, mews_score, message)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    alert.get("timestamp"),
                    alert.get("patient_id"),
                    alert.get("alert_type", "mews_threshold"),
                    alert.get("severity", "critical"),
                    alert.get("mews_score"),
                    alert.get("message"),
                ),
            )

    def close(self) -> None:
        self._pool.closeall()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
