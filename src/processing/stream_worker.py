"""Lightweight pure-Python stream worker: the no-Spark processing path.

Spark Structured Streaming (stream_processor.py) is the horizontal-scale
path, but it is a heavy dependency for a single-node deployment and it is
awkward for stateful per-patient anomaly detection. This worker does the
same job with a plain Kafka consumer loop:

  vitals.raw -> MEWS scoring -> anomaly detection
    -> vitals.scored topic (every scored reading)
    -> vitals.alerts topic + alerts table (MEWS >= threshold, anomalies)
    -> TimescaleDB (vitals_raw + vitals_scored)
    -> Redis pub/sub (live feed for the API's WebSocket endpoint)

The processing core (VitalsProcessor) is transport-free: it takes a dict in
and returns a ProcessedReading out, so it is fully unit-testable without
Kafka, Postgres, or Redis running. The run loop wires it to real
infrastructure and batches database writes.

Usage:
    python -m src.processing.stream_worker
    python -m src.processing.stream_worker --batch-size 100
"""

from __future__ import annotations

import json
import logging
import signal
from dataclasses import dataclass, field

import click

from ..config.settings import settings
from .anomaly_detector import Anomaly, VitalsAnomalyDetector
from .mews_calculator import MEWSResult, calculate_mews_from_dict

logger = logging.getLogger(__name__)


@dataclass
class ProcessedReading:
    """A vitals reading enriched with MEWS scores and anomaly flags."""

    reading: dict
    mews: MEWSResult
    anomalies: list[Anomaly] = field(default_factory=list)

    suppress_mews_alert: bool = False

    @property
    def scored_record(self) -> dict:
        """The reading merged with its MEWS breakdown, ready for storage."""
        return {
            **self.reading,
            "mews_score": self.mews.total,
            "mews_hr": self.mews.hr_score,
            "mews_sbp": self.mews.sbp_score,
            "mews_rr": self.mews.rr_score,
            "mews_temp": self.mews.temp_score,
            "mews_avpu": self.mews.avpu_score,
            "severity": self.mews.severity,
        }

    @property
    def alerts(self) -> list[dict]:
        """Alert records this reading should generate (may be empty)."""
        out = []
        if (
            self.mews.total >= settings.mews_alert_threshold
            and not self.suppress_mews_alert
        ):
            out.append({
                "timestamp": self.reading.get("timestamp"),
                "patient_id": self.reading.get("patient_id"),
                "alert_type": "mews_threshold",
                "severity": "critical",
                "mews_score": self.mews.total,
                "message": (
                    f"MEWS {self.mews.total} >= {settings.mews_alert_threshold}: "
                    f"immediate clinical review required "
                    f"(HR {self.mews.hr_score}, SBP {self.mews.sbp_score}, "
                    f"RR {self.mews.rr_score}, Temp {self.mews.temp_score}, "
                    f"AVPU {self.mews.avpu_score})"
                ),
            })
        for anomaly in self.anomalies:
            out.append({
                "timestamp": anomaly.timestamp,
                "patient_id": anomaly.patient_id,
                "alert_type": f"anomaly_{anomaly.rule_violated}",
                "severity": "warning",
                "mews_score": self.mews.total,
                "message": anomaly.message,
            })
        return out


class VitalsProcessor:
    """Transport-free processing core: score, detect, decide.

    Holds the per-patient anomaly detector state. Feed it readings in
    arrival order (Kafka's patient_id keying guarantees per-patient order).
    """

    # readings below threshold required before a patient's alarm re-arms;
    # without this latch a patient sitting at MEWS 6 fires one alert per
    # reading, and a feed full of duplicates is a feed nobody reads
    REARM_READINGS = 20

    def __init__(
        self,
        window_size: int | None = None,
        z_threshold: float | None = None,
    ):
        self.detector = VitalsAnomalyDetector(
            window_size=window_size or settings.anomaly_window_size,
            z_threshold=z_threshold or settings.anomaly_z_threshold,
        )
        self.processed_count = 0
        self.alert_count = 0
        self._latched: dict[str, bool] = {}
        self._below_count: dict[str, int] = {}

    def process(self, reading: dict) -> ProcessedReading:
        """Score one reading and update per-patient anomaly + alarm state."""
        mews = calculate_mews_from_dict(reading)
        patient_id = reading.get("patient_id", "unknown")
        anomalies = self.detector.update(patient_id, reading)

        suppress = False
        if mews.total >= settings.mews_alert_threshold:
            suppress = self._latched.get(patient_id, False)
            self._latched[patient_id] = True
            self._below_count[patient_id] = 0
        else:
            below = self._below_count.get(patient_id, 0) + 1
            self._below_count[patient_id] = below
            if below >= self.REARM_READINGS:
                self._latched[patient_id] = False

        result = ProcessedReading(
            reading=reading, mews=mews, anomalies=anomalies,
            suppress_mews_alert=suppress,
        )
        self.processed_count += 1
        self.alert_count += len(result.alerts)
        return result


class StreamWorker:
    """Wire the processing core to Kafka, TimescaleDB, and Redis."""

    def __init__(self, batch_size: int = 50):
        from confluent_kafka import Consumer, Producer  # heavy import, defer

        self.batch_size = batch_size
        self.processor = VitalsProcessor()
        self._running = False

        self._consumer = Consumer({
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": settings.kafka_group_id,
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,
        })
        self._producer = Producer({
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "client.id": "vitals-stream-worker",
            "linger.ms": 10,
        })

        from ..storage.timescale_writer import TimescaleWriter
        self._writer = TimescaleWriter()

        self._redis = None
        try:
            import redis
            self._redis = redis.Redis(
                host=settings.redis_host, port=settings.redis_port,
                socket_connect_timeout=2,
            )
            self._redis.ping()
        except Exception as exc:  # noqa: BLE001 - degrade, don't die
            logger.warning(f"Redis unavailable, live feed disabled: {exc}")
            self._redis = None

    def run(self) -> None:
        """Consume vitals.raw and process until interrupted."""
        self._consumer.subscribe([settings.kafka_raw_topic])
        self._running = True
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

        raw_batch: list[dict] = []
        scored_batch: list[dict] = []
        logger.info(
            f"Stream worker consuming {settings.kafka_raw_topic} "
            f"(batch_size={self.batch_size})"
        )

        while self._running:
            msg = self._consumer.poll(timeout=1.0)
            if msg is None:
                if raw_batch:
                    self._flush(raw_batch, scored_batch)
                    raw_batch, scored_batch = [], []
                continue
            if msg.error():
                logger.error(f"Consumer error: {msg.error()}")
                continue

            try:
                reading = json.loads(msg.value())
            except json.JSONDecodeError:
                logger.warning("Dropping malformed message")
                continue

            result = self.processor.process(reading)
            raw_batch.append(reading)
            scored_batch.append(result.scored_record)
            self._publish(result)

            if len(raw_batch) >= self.batch_size:
                self._flush(raw_batch, scored_batch)
                raw_batch, scored_batch = [], []
                self._consumer.commit(asynchronous=True)

        self._flush(raw_batch, scored_batch)
        self._consumer.close()
        self._producer.flush(10)
        logger.info(
            f"Worker stopped. Processed {self.processor.processed_count} "
            f"readings, generated {self.processor.alert_count} alerts."
        )

    def _publish(self, result: ProcessedReading) -> None:
        """Publish scored reading + alerts to Kafka and Redis."""
        key = result.reading.get("patient_id", "unknown").encode()
        scored = json.dumps(result.scored_record).encode()

        self._producer.produce(settings.kafka_scored_topic, key=key, value=scored)
        for alert in result.alerts:
            self._producer.produce(
                settings.kafka_alerts_topic, key=key,
                value=json.dumps(alert).encode(),
            )
            self._writer.write_alert(alert)
        self._producer.poll(0)

        if self._redis is not None:
            try:
                self._redis.publish(
                    f"vitals:{result.reading.get('patient_id')}", scored
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Redis publish failed: {exc}")

    def _flush(self, raw: list[dict], scored: list[dict]) -> None:
        if not raw:
            return
        self._writer.write_vitals_batch(raw)
        self._writer.write_scored_batch(scored)

    def _stop(self, *_args) -> None:
        logger.info("Shutdown signal received, draining...")
        self._running = False


@click.command()
@click.option("--batch-size", default=50, help="DB write batch size")
def main(batch_size: int):
    """Run the lightweight stream worker."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    StreamWorker(batch_size=batch_size).run()


if __name__ == "__main__":
    main()
