"""Spark Structured Streaming job for real-time vitals processing.

Consumes raw vitals readings from Kafka, enriches each reading with MEWS
scores and anomaly flags, performs sliding window aggregations, and writes
results to both downstream Kafka topics and TimescaleDB.

Processing pipeline:
  vitals.raw → parse JSON → MEWS scoring → anomaly detection
    → vitals.scored (all scored readings)
    → vitals.alerts (MEWS >= threshold only)
    → TimescaleDB (all scored readings for persistence)

Windowed aggregations:
  5-minute sliding window, 1-minute slide, per patient:
    avg, min, max, stddev for each vital sign parameter
"""

from __future__ import annotations

import logging

import click
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType,
    StringType,
    StructField,
    StructType,
)

from ..config.settings import settings

logger = logging.getLogger(__name__)

# Schema for raw vitals JSON messages
VITALS_SCHEMA = StructType([
    StructField("patient_id", StringType(), False),
    StructField("timestamp", StringType(), False),
    StructField("heart_rate", DoubleType(), True),
    StructField("systolic_bp", DoubleType(), True),
    StructField("diastolic_bp", DoubleType(), True),
    StructField("respiratory_rate", DoubleType(), True),
    StructField("spo2", DoubleType(), True),
    StructField("temperature", DoubleType(), True),
    StructField("avpu", StringType(), True),
    StructField("device_id", StringType(), True),
    StructField("unit", StringType(), True),
])


def create_spark_session() -> SparkSession:
    """Create a SparkSession configured for Kafka structured streaming."""
    return (
        SparkSession.builder
        .appName("VitalsStreamProcessor")
        .config("spark.jars.packages",
                "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,"
                "org.postgresql:postgresql:42.7.1")
        .config("spark.sql.streaming.checkpointLocation",
                "/tmp/spark-checkpoints/vitals")
        .config("spark.sql.shuffle.partitions", "6")
        .getOrCreate()
    )


def read_raw_stream(spark: SparkSession) -> DataFrame:
    """Read the raw vitals stream from Kafka."""
    return (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", settings.kafka_bootstrap_servers)
        .option("subscribe", settings.kafka_raw_topic)
        .option("startingOffsets", "latest")
        .option("failOnDataLoss", "false")
        .load()
        .select(
            F.from_json(
                F.col("value").cast("string"), VITALS_SCHEMA
            ).alias("data")
        )
        .select("data.*")
        .withColumn("event_time", F.to_timestamp("timestamp"))
    )


def add_mews_scores(df: DataFrame) -> DataFrame:
    """Add MEWS scores to each vitals reading using a pandas UDF.

    In production, this would use a pandas_udf for vectorized execution.
    For the scaffolding, we define the column expressions directly.
    """
    # MEWS Heart Rate scoring
    hr_score = (
        F.when(F.col("heart_rate") >= 130, 3)
        .when(F.col("heart_rate") >= 111, 2)
        .when(F.col("heart_rate") >= 101, 1)
        .when(F.col("heart_rate") >= 51, 0)
        .when(F.col("heart_rate") >= 41, 1)
        .when(F.col("heart_rate") >= 1, 2)
        .otherwise(0)
    )

    # MEWS Systolic BP scoring
    sbp_score = (
        F.when(F.col("systolic_bp") >= 200, 2)
        .when(F.col("systolic_bp") >= 101, 0)
        .when(F.col("systolic_bp") >= 81, 1)
        .when(F.col("systolic_bp") >= 71, 2)
        .otherwise(3)
    )

    # MEWS Respiratory Rate scoring
    rr_score = (
        F.when(F.col("respiratory_rate") >= 30, 3)
        .when(F.col("respiratory_rate") >= 21, 2)
        .when(F.col("respiratory_rate") >= 15, 1)
        .when(F.col("respiratory_rate") >= 9, 0)
        .when(F.col("respiratory_rate") >= 1, 2)
        .otherwise(0)
    )

    # MEWS Temperature scoring
    temp_score = (
        F.when(F.col("temperature") >= 38.5, 2)
        .when(F.col("temperature") >= 35.0, 0)
        .otherwise(2)
    )

    # MEWS AVPU scoring
    avpu_score = (
        F.when(F.upper(F.col("avpu")) == "U", 3)
        .when(F.upper(F.col("avpu")) == "P", 2)
        .when(F.upper(F.col("avpu")) == "V", 1)
        .otherwise(0)
    )

    return (
        df
        .withColumn("mews_hr", hr_score)
        .withColumn("mews_sbp", sbp_score)
        .withColumn("mews_rr", rr_score)
        .withColumn("mews_temp", temp_score)
        .withColumn("mews_avpu", avpu_score)
        .withColumn("mews_score",
                     F.col("mews_hr") + F.col("mews_sbp") +
                     F.col("mews_rr") + F.col("mews_temp") +
                     F.col("mews_avpu"))
    )


def window_aggregations(df: DataFrame) -> DataFrame:
    """Compute sliding window aggregations per patient.

    5-minute window, 1-minute slide. Computes avg, min, max, stddev
    for each vital sign parameter.
    """
    return (
        df
        .withWatermark("event_time", "2 minutes")
        .groupBy(
            F.window("event_time", "5 minutes", "1 minute"),
            "patient_id",
        )
        .agg(
            F.avg("heart_rate").alias("hr_avg"),
            F.min("heart_rate").alias("hr_min"),
            F.max("heart_rate").alias("hr_max"),
            F.stddev("heart_rate").alias("hr_stddev"),
            F.avg("systolic_bp").alias("sbp_avg"),
            F.avg("spo2").alias("spo2_avg"),
            F.min("spo2").alias("spo2_min"),
            F.avg("respiratory_rate").alias("rr_avg"),
            F.avg("temperature").alias("temp_avg"),
            F.avg("mews_score").alias("mews_avg"),
            F.max("mews_score").alias("mews_max"),
            F.count("*").alias("reading_count"),
        )
    )


def write_scored_to_timescale(batch_df: DataFrame, batch_id: int) -> None:
    """foreachBatch sink: persist each micro-batch of scored readings.

    Runs on the driver per micro-batch; TimescaleWriter batches the insert
    with execute_values so a 10-second trigger stays one round trip.
    """
    from ..storage.timescale_writer import TimescaleWriter

    rows = [row.asDict() for row in batch_df.collect()]
    if not rows:
        return
    with TimescaleWriter() as writer:
        writer.write_scored_batch(rows)
    logger.info(f"Batch {batch_id}: wrote {len(rows)} scored readings")


@click.command()
@click.option("--trigger-interval", default="10 seconds", help="Spark trigger interval")
def main(trigger_interval: str):
    """Run the Spark Structured Streaming vitals processor."""
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting vitals stream processor")

    spark = create_spark_session()

    # Read raw stream
    raw = read_raw_stream(spark)

    # Add MEWS scores
    scored = add_mews_scores(raw)

    # Write scored readings to Kafka (handle owned by spark.streams)
    (
        scored
        .select(
            F.col("patient_id").alias("key"),
            F.to_json(F.struct("*")).alias("value"),
        )
        .writeStream
        .format("kafka")
        .option("kafka.bootstrap.servers", settings.kafka_bootstrap_servers)
        .option("topic", settings.kafka_scored_topic)
        .option("checkpointLocation", "/tmp/spark-checkpoints/scored")
        .trigger(processingTime=trigger_interval)
        .start()
    )

    # Write alerts to Kafka (MEWS >= threshold)
    alerts = scored.filter(F.col("mews_score") >= settings.mews_alert_threshold)
    (
        alerts
        .select(
            F.col("patient_id").alias("key"),
            F.to_json(F.struct("*")).alias("value"),
        )
        .writeStream
        .format("kafka")
        .option("kafka.bootstrap.servers", settings.kafka_bootstrap_servers)
        .option("topic", settings.kafka_alerts_topic)
        .option("checkpointLocation", "/tmp/spark-checkpoints/alerts")
        .trigger(processingTime=trigger_interval)
        .start()
    )

    # Persist scored readings to TimescaleDB via foreachBatch
    (
        scored
        .withColumn("time", F.col("event_time"))
        .drop("event_time")
        .writeStream
        .foreachBatch(write_scored_to_timescale)
        .option("checkpointLocation", "/tmp/spark-checkpoints/timescale")
        .trigger(processingTime=trigger_interval)
        .start()
    )

    logger.info("Stream processor running — awaiting termination")
    spark.streams.awaitAnyTermination()


if __name__ == "__main__":
    main()
