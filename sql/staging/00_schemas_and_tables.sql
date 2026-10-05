-- Schemas and landing/staging tables.
-- raw     : exact copy of the source file (all text) so we can always trace back to what was delivered
-- staging : typed, renamed, validated rows (one row per customer, still wide like the source)
-- audit   : pipeline run log and data-quality results (kept across runs)
-- core / features / ml / mart are built by the later SQL files

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS features;
CREATE SCHEMA IF NOT EXISTS ml;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS audit;

-- ---------------------------------------------------------------- audit (persistent)
CREATE TABLE IF NOT EXISTS audit.pipeline_runs (
    run_id          SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',
    source_file     TEXT,
    file_sha256     TEXT,
    rows_read       INTEGER,
    rows_loaded     INTEGER,
    rows_rejected   INTEGER,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS audit.dq_check_results (
    run_id              INTEGER NOT NULL REFERENCES audit.pipeline_runs(run_id),
    check_name          TEXT NOT NULL,
    severity            TEXT NOT NULL CHECK (severity IN ('ERROR', 'WARN', 'INFO')),
    level               TEXT NOT NULL,
    description         TEXT,
    rows_failed         INTEGER NOT NULL,
    pct_failed          NUMERIC(7, 3),
    passed              BOOLEAN NOT NULL,
    sample_customer_ids TEXT,
    PRIMARY KEY (run_id, check_name)
);

-- Row-level flags from the latest run only (replaced on every run)
CREATE TABLE IF NOT EXISTS audit.dq_row_flags (
    run_id              INTEGER NOT NULL,
    source_row_number   INTEGER NOT NULL,
    customer_id         BIGINT,
    check_name          TEXT NOT NULL,
    severity            TEXT NOT NULL
);

-- ---------------------------------------------------------------- raw (rebuilt each run)
DROP TABLE IF EXISTS raw.credit_card_clients;
CREATE TABLE raw.credit_card_clients (
    source_row_number INTEGER PRIMARY KEY,
    "ID" TEXT, "LIMIT_BAL" TEXT, "SEX" TEXT, "EDUCATION" TEXT, "MARRIAGE" TEXT, "AGE" TEXT,
    "PAY_0" TEXT, "PAY_2" TEXT, "PAY_3" TEXT, "PAY_4" TEXT, "PAY_5" TEXT, "PAY_6" TEXT,
    "BILL_AMT1" TEXT, "BILL_AMT2" TEXT, "BILL_AMT3" TEXT, "BILL_AMT4" TEXT, "BILL_AMT5" TEXT, "BILL_AMT6" TEXT,
    "PAY_AMT1" TEXT, "PAY_AMT2" TEXT, "PAY_AMT3" TEXT, "PAY_AMT4" TEXT, "PAY_AMT5" TEXT, "PAY_AMT6" TEXT,
    "default payment next month" TEXT,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- staging (rebuilt each run)
-- Source suffixes are confusing (PAY_0 = Sep, PAY_6 = Apr, there is no PAY_1).
-- Staging renames them to m1 (Apr 2005) ... m6 (Sep 2005) so later SQL is readable.
DROP TABLE IF EXISTS staging.stg_credit_card_clients;
CREATE TABLE staging.stg_credit_card_clients (
    customer_id         BIGINT PRIMARY KEY,
    credit_limit        NUMERIC(12, 2) NOT NULL CHECK (credit_limit > 0),
    sex_code            SMALLINT NOT NULL,
    education_code      SMALLINT NOT NULL,
    marriage_code       SMALLINT NOT NULL,
    age                 SMALLINT NOT NULL CHECK (age BETWEEN 18 AND 100),
    status_m1 SMALLINT NOT NULL, status_m2 SMALLINT NOT NULL, status_m3 SMALLINT NOT NULL,
    status_m4 SMALLINT NOT NULL, status_m5 SMALLINT NOT NULL, status_m6 SMALLINT NOT NULL,
    bill_m1 NUMERIC(12, 2) NOT NULL, bill_m2 NUMERIC(12, 2) NOT NULL, bill_m3 NUMERIC(12, 2) NOT NULL,
    bill_m4 NUMERIC(12, 2) NOT NULL, bill_m5 NUMERIC(12, 2) NOT NULL, bill_m6 NUMERIC(12, 2) NOT NULL,
    payment_m1 NUMERIC(12, 2) NOT NULL CHECK (payment_m1 >= 0), payment_m2 NUMERIC(12, 2) NOT NULL CHECK (payment_m2 >= 0),
    payment_m3 NUMERIC(12, 2) NOT NULL CHECK (payment_m3 >= 0), payment_m4 NUMERIC(12, 2) NOT NULL CHECK (payment_m4 >= 0),
    payment_m5 NUMERIC(12, 2) NOT NULL CHECK (payment_m5 >= 0), payment_m6 NUMERIC(12, 2) NOT NULL CHECK (payment_m6 >= 0),
    default_next_month  SMALLINT NOT NULL CHECK (default_next_month IN (0, 1)),
    source_row_number   INTEGER NOT NULL,
    load_run_id         INTEGER NOT NULL
);

DROP TABLE IF EXISTS staging.rejected_rows;
CREATE TABLE staging.rejected_rows (
    source_row_number INTEGER PRIMARY KEY,
    raw_record        JSONB NOT NULL,
    reject_reasons    TEXT NOT NULL,
    load_run_id       INTEGER NOT NULL
);
