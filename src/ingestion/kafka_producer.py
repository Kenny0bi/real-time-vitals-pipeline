"""Kafka producer for publishing patient vitals readings.

Produces JSON-serialized vitals messages to the `vitals.raw` topic, keyed
by patient_id to ensure all readings for a single patient land on the same
partition (preserving per-patient ordering).

Uses the confluent-kafka Python client with delivery callbacks for
reliability monitoring.
"""

from __future__ import annotations

import json
import logging

from confluent_kafka import Producer

from ..config.settings import settings

logger = logging.getLogger(__name__)


class VitalsProducer:
    """Produce patient vitals readings to Kafka."""

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        topic: str | None = None,
    ):
        self.topic = topic or settings.kafka_raw_topic
        self._producer = Producer({
            "bootstrap.servers": bootstrap_servers or settings.kafka_bootstrap_servers,
            "client.id": "vitals-simulator",
            "acks": "all",
            "enable.idempotence": True,
            "max.in.flight.requests.per.connection": 5,
            "compression.type": "snappy",
            "linger.ms": 10,
            "batch.size": 65536,
        })
        self._delivery_count = 0
        self._error_count = 0

    def send(self, reading: dict) -> None:
        """Send a single vitals reading to Kafka.

        The message is keyed by patient_id for partition affinity.
        """
        key = reading.get("patient_id", "unknown")
        value = json.dumps(reading).encode("utf-8")

        self._producer.produce(
            topic=self.topic,
            key=key.encode("utf-8"),
            value=value,
            callback=self._delivery_callback,
        )

        # Trigger delivery callbacks periodically
        self._producer.poll(0)

    def _delivery_callback(self, err, msg):
        """Handle delivery confirmation or failure."""
        if err is not None:
            self._error_count += 1
            logger.error(
                f"Delivery failed for {msg.key()}: {err}"
            )
        else:
            self._delivery_count += 1
            if self._delivery_count % 1000 == 0:
                logger.info(
                    f"Delivered {self._delivery_count} messages "
                    f"({self._error_count} errors)"
                )

    def flush(self, timeout: float = 30.0) -> int:
        """Flush all pending messages. Returns number of messages still in queue."""
        remaining = self._producer.flush(timeout)
        logger.info(
            f"Producer flushed. Delivered: {self._delivery_count}, "
            f"Errors: {self._error_count}, Remaining: {remaining}"
        )
        return remaining

    @property
    def stats(self) -> dict:
        return {
            "delivered": self._delivery_count,
            "errors": self._error_count,
        }
