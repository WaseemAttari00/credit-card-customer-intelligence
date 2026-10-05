"""Runs the whole project end to end.

    python -m src.pipeline                 # all steps
    python -m src.pipeline --steps sql     # just some steps (comma separated)

Steps (in order):
    ingest   extract the UCI file, validate it, load raw/staging/audit tables
    sql      build core tables, features and descriptive marts in Postgres
    model    train/evaluate PD and dormancy models, score customers, SHAP, segmentation
    marts    build the marts that depend on model scores (profitability, customer 360, priority)
    export   analysis tables, significance tests and the data-quality report
"""
from __future__ import annotations

import argparse
import logging
import time
import warnings

from src.config import path
from src.db import connect, run_sql_file

log = logging.getLogger("pipeline")

# SQL that only depends on staging data
SQL_CORE = [
    "sql/transformations/01_dim_month.sql",
    "sql/transformations/02_dim_customer.sql",
    "sql/transformations/03_fact_account_month.sql",
    "sql/transformations/04_fact_default_outcome.sql",
    "sql/features/01_customer_risk_features.sql",
    "sql/features/02_churn_snapshots.sql",
    "sql/marts/01_customer_monthly_metrics.sql",
    "sql/marts/02_portfolio_monthly.sql",
    "sql/marts/03_roll_rates.sql",
    "sql/marts/04_delinquency_cohorts.sql",
]
# SQL that needs model outputs (ml.* tables) and the assumptions table
SQL_MARTS = [
    "sql/marts/05_customer_profitability.sql",
    "sql/marts/06_customer_360.sql",
    "sql/marts/07_retention_priority.sql",
    "sql/marts/08_dashboard_curves.sql",
]


def run_sql_list(files: list[str]) -> None:
    with connect() as conn:
        for f in files:
            run_sql_file(conn, path(f))


def step_ingest():
    from src.ingestion.run_ingestion import run
    run()


def step_sql():
    run_sql_list(SQL_CORE)


def step_model():
    from src.modeling.train import run
    run()


def step_marts():
    run_sql_list(SQL_MARTS)


def step_export():
    from src.analytics.export import run
    run()


STEPS = {"ingest": step_ingest, "sql": step_sql, "model": step_model, "marts": step_marts, "export": step_export}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", default=",".join(STEPS), help="comma-separated subset of: " + ", ".join(STEPS))
    args = parser.parse_args(argv)
    # joblib/loky can't count physical cores on newer Windows (no `wmic`); harmless
    warnings.filterwarnings("ignore", message=".*physical cores.*")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in [s.strip() for s in args.steps.split(",") if s.strip()]:
        if name not in STEPS:
            raise SystemExit(f"unknown step {name!r}")
        t0 = time.time()
        log.info("=== step %s ===", name)
        STEPS[name]()
        log.info("=== step %s done in %.1fs ===", name, time.time() - t0)


if __name__ == "__main__":
    main()
