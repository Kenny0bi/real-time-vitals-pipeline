-- TimescaleDB schema for Real-Time Patient Vitals Pipeline
-- Creates hypertables for time-series storage and continuous aggregates
-- for efficient rollup queries.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Raw vitals readings
CREATE TABLE IF NOT EXISTS vitals_raw (
    time             TIMESTAMPTZ NOT NULL,
    patient_id       TEXT NOT NULL,
    heart_rate       DOUBLE PRECISION,
    systolic_bp      DOUBLE PRECISION,
    diastolic_bp     DOUBLE PRECISION,
    respiratory_rate DOUBLE PRECISION,
    spo2             DOUBLE PRECISION,
    temperature      DOUBLE PRECISION,
    avpu             VARCHAR(1),
    device_id        TEXT,
    unit             TEXT
);
SELECT create_hypertable('vitals_raw', 'time', if_not_exists => TRUE);

-- Scored vitals with MEWS
CREATE TABLE IF NOT EXISTS vitals_scored (
    time             TIMESTAMPTZ NOT NULL,
    patient_id       TEXT NOT NULL,
    heart_rate       DOUBLE PRECISION,
    systolic_bp      DOUBLE PRECISION,
    diastolic_bp     DOUBLE PRECISION,
    respiratory_rate DOUBLE PRECISION,
    spo2             DOUBLE PRECISION,
    temperature      DOUBLE PRECISION,
    avpu             VARCHAR(1),
    mews_score       INT NOT NULL,
    mews_hr          INT,
    mews_sbp         INT,
    mews_rr          INT,
    mews_temp        INT,
    mews_avpu        INT,
    device_id        TEXT,
    unit             TEXT
);
SELECT create_hypertable('vitals_scored', 'time', if_not_exists => TRUE);

-- Clinical alerts
CREATE TABLE IF NOT EXISTS alerts (
    id               SERIAL,
    time             TIMESTAMPTZ NOT NULL,
    patient_id       TEXT NOT NULL,
    alert_type       TEXT NOT NULL,
    severity         TEXT NOT NULL,
    mews_score       INT,
    message          TEXT,
    acknowledged     BOOLEAN DEFAULT FALSE,
    acknowledged_by  TEXT,
    acknowledged_at  TIMESTAMPTZ,
    PRIMARY KEY (id, time)
);
SELECT create_hypertable('alerts', 'time', if_not_exists => TRUE);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_vitals_raw_patient ON vitals_raw (patient_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_vitals_scored_patient ON vitals_scored (patient_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_vitals_scored_mews ON vitals_scored (mews_score, time DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_patient ON alerts (patient_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts (severity, time DESC);

-- 5-minute continuous aggregate
CREATE MATERIALIZED VIEW IF NOT EXISTS vitals_5min
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('5 minutes', time) AS bucket,
    patient_id,
    avg(heart_rate)       AS hr_avg,
    min(heart_rate)       AS hr_min,
    max(heart_rate)       AS hr_max,
    stddev(heart_rate)    AS hr_stddev,
    avg(systolic_bp)      AS sbp_avg,
    min(systolic_bp)      AS sbp_min,
    max(systolic_bp)      AS sbp_max,
    avg(diastolic_bp)     AS dbp_avg,
    avg(spo2)             AS spo2_avg,
    min(spo2)             AS spo2_min,
    avg(respiratory_rate) AS rr_avg,
    avg(temperature)      AS temp_avg,
    count(*)              AS reading_count
FROM vitals_raw
GROUP BY bucket, patient_id
WITH NO DATA;

-- 1-hour continuous aggregate
CREATE MATERIALIZED VIEW IF NOT EXISTS vitals_1hr
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    patient_id,
    avg(heart_rate)       AS hr_avg,
    min(heart_rate)       AS hr_min,
    max(heart_rate)       AS hr_max,
    stddev(heart_rate)    AS hr_stddev,
    avg(systolic_bp)      AS sbp_avg,
    min(systolic_bp)      AS sbp_min,
    max(systolic_bp)      AS sbp_max,
    avg(spo2)             AS spo2_avg,
    min(spo2)             AS spo2_min,
    avg(respiratory_rate) AS rr_avg,
    avg(temperature)      AS temp_avg,
    count(*)              AS reading_count
FROM vitals_raw
GROUP BY bucket, patient_id
WITH NO DATA;

-- Refresh policies
SELECT add_continuous_aggregate_policy('vitals_5min',
    start_offset => INTERVAL '1 hour',
    end_offset   => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);

SELECT add_continuous_aggregate_policy('vitals_1hr',
    start_offset => INTERVAL '3 hours',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- Retention policy: drop raw data older than 30 days
SELECT add_retention_policy('vitals_raw', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_retention_policy('vitals_scored', INTERVAL '90 days', if_not_exists => TRUE);
