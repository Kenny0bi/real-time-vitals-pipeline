"""Centralized configuration for the Real-Time Vitals Pipeline."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_group_id: str = "vitals-processor"
    kafka_raw_topic: str = "vitals.raw"
    kafka_scored_topic: str = "vitals.scored"
    kafka_alerts_topic: str = "vitals.alerts"

    # TimescaleDB
    timescale_host: str = "localhost"
    timescale_port: int = 5432
    timescale_user: str = "vitals"
    timescale_password: str = "vitals_dev"
    timescale_db: str = "patient_vitals"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # Simulator
    simulator_patients: int = 20
    simulator_rate: float = 1.0
    simulator_deterioration_prob: float = 0.2

    # Processing
    mews_alert_threshold: int = 5
    anomaly_window_size: int = 30
    anomaly_z_threshold: float = 3.0

    # General
    log_level: str = "INFO"
    project_root: Path = Path(__file__).resolve().parents[2]

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8"
    )

    @property
    def timescale_url(self) -> str:
        return (
            f"postgresql://{self.timescale_user}:{self.timescale_password}"
            f"@{self.timescale_host}:{self.timescale_port}/{self.timescale_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"


settings = Settings()
