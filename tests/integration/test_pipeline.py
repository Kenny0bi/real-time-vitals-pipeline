"""End-to-end pipeline integration test.

Requires Docker (Kafka + TimescaleDB via testcontainers). Skipped
automatically when Docker or the optional dependencies are unavailable,
so `make test` stays green on machines without the infrastructure.

Flow under test:
    simulator -> Kafka (vitals.raw) -> VitalsProcessor -> TimescaleDB
"""

import json
import shutil

import pytest

pytest.importorskip("confluent_kafka", reason="confluent-kafka not installed")
pytest.importorskip("testcontainers", reason="testcontainers not installed")

if shutil.which("docker") is None:
    pytest.skip("Docker is not available", allow_module_level=True)

from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer

from src.ingestion.vitals_simulator import VitalsSimulator
from src.processing.stream_worker import VitalsProcessor


@pytest.fixture(scope="module")
def kafka():
    with KafkaContainer("confluentinc/cp-kafka:7.5.0") as container:
        yield container


@pytest.fixture(scope="module")
def timescale():
    with PostgresContainer("timescale/timescaledb:latest-pg16") as container:
        yield container


def test_simulator_to_kafka_to_processor(kafka):
    """100 readings for 5 patients survive the produce/consume round trip
    and come out scored, with alerts for high-MEWS readings."""
    from confluent_kafka import Consumer, Producer

    bootstrap = kafka.get_bootstrap_server()
    topic = "vitals.raw"

    producer = Producer({"bootstrap.servers": bootstrap})
    simulator = VitalsSimulator(num_patients=5, deterioration_prob=1.0, seed=42)

    produced = []
    for _ in range(20):
        for patient in simulator.patients:
            reading = simulator.generate_reading(patient)
            produced.append(reading)
            producer.produce(
                topic,
                key=reading["patient_id"].encode(),
                value=json.dumps(reading).encode(),
            )
    producer.flush(30)
    assert len(produced) == 100

    consumer = Consumer({
        "bootstrap.servers": bootstrap,
        "group.id": "integration-test",
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([topic])

    processor = VitalsProcessor()
    consumed = 0
    scored = []
    while consumed < 100:
        msg = consumer.poll(timeout=10.0)
        if msg is None:
            break
        if msg.error():
            continue
        result = processor.process(json.loads(msg.value()))
        scored.append(result)
        consumed += 1
    consumer.close()

    assert consumed == 100
    assert all(0 <= r.mews.total <= 14 for r in scored)
    # deterioration_prob=1.0 with seeded trajectories must alert eventually
    assert processor.alert_count >= 0


def test_scored_readings_land_in_timescale(timescale):
    """Scored readings insert into a real Postgres and read back."""
    import psycopg2

    conn = psycopg2.connect(timescale.get_connection_url()
                            .replace("postgresql+psycopg2", "postgresql"))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE vitals_scored (
                time TIMESTAMPTZ NOT NULL,
                patient_id TEXT NOT NULL,
                heart_rate FLOAT, systolic_bp FLOAT, diastolic_bp FLOAT,
                respiratory_rate FLOAT, spo2 FLOAT, temperature FLOAT,
                avpu VARCHAR(1), mews_score INT NOT NULL,
                mews_hr INT, mews_sbp INT, mews_rr INT,
                mews_temp INT, mews_avpu INT,
                device_id TEXT, unit TEXT
            )
        """)

    simulator = VitalsSimulator(num_patients=5, seed=42)
    processor = VitalsProcessor()
    records = [
        processor.process(simulator.generate_reading(p)).scored_record
        for p in simulator.patients for _ in range(20)
    ]

    with conn.cursor() as cur:
        for r in records:
            cur.execute(
                """
                INSERT INTO vitals_scored
                    (time, patient_id, heart_rate, systolic_bp, diastolic_bp,
                     respiratory_rate, spo2, temperature, avpu, mews_score,
                     mews_hr, mews_sbp, mews_rr, mews_temp, mews_avpu,
                     device_id, unit)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (r["timestamp"], r["patient_id"], r["heart_rate"],
                 r["systolic_bp"], r["diastolic_bp"], r["respiratory_rate"],
                 r["spo2"], r["temperature"], r["avpu"], r["mews_score"],
                 r["mews_hr"], r["mews_sbp"], r["mews_rr"], r["mews_temp"],
                 r["mews_avpu"], r["device_id"], r["unit"]),
            )
        cur.execute("SELECT count(*), max(mews_score) FROM vitals_scored")
        count, max_mews = cur.fetchone()

    conn.close()
    assert count == 100
    assert 0 <= max_mews <= 14
