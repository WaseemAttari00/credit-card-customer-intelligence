"""Loading: raw landing table, data-quality results, typed staging table, quarantine table."""
from __future__ import annotations

import json
import logging

import pandas as pd
import psycopg

from src.config import load_config
from src.db import copy_dataframe

log = logging.getLogger(__name__)


def staging_column_map() -> dict[str, str]:
    """Source column -> staging column. Months are re-indexed m1 (Apr) .. m6 (Sep)."""
    mapping = {"ID": "customer_id", "LIMIT_BAL": "credit_limit", "SEX": "sex_code",
               "EDUCATION": "education_code", "MARRIAGE": "marriage_code", "AGE": "age",
               "default payment next month": "default_next_month",
               "source_row_number": "source_row_number"}
    for m in load_config()["months"]:
        i = m["month_index"]
        mapping[m["status_col"]] = f"status_m{i}"
        mapping[m["bill_col"]] = f"bill_m{i}"
        mapping[m["pay_col"]] = f"payment_m{i}"
    return mapping


def start_run(conn: psycopg.Connection, meta: dict) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO audit.pipeline_runs (source_file, file_sha256, rows_read) "
                    "VALUES (%s, %s, %s) RETURNING run_id",
                    (meta["source_file"], meta["file_sha256"], meta["rows_read"]))
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn: psycopg.Connection, run_id: int, status: str, loaded: int | None = None,
               rejected: int | None = None, notes: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE audit.pipeline_runs SET finished_at = now(), status = %s, rows_loaded = %s, "
                    "rows_rejected = %s, notes = %s WHERE run_id = %s",
                    (status, loaded, rejected, notes, run_id))
    conn.commit()


def load_raw(conn: psycopg.Connection, raw: pd.DataFrame) -> int:
    df = raw.copy()
    df.columns = [f'"{c}"' if c != "source_row_number" else c for c in df.columns]
    return copy_dataframe(conn, df, "raw.credit_card_clients")


def write_dq_results(conn: psycopg.Connection, run_id: int, results: pd.DataFrame, flags: pd.DataFrame) -> None:
    res = results.assign(run_id=run_id)[["run_id", "check_name", "severity", "level", "description",
                                         "rows_failed", "pct_failed", "passed", "sample_customer_ids"]]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM audit.dq_check_results WHERE run_id = %s", (run_id,))
    copy_dataframe(conn, res, "audit.dq_check_results", truncate=False)
    fl = flags.assign(run_id=run_id)[["run_id", "source_row_number", "customer_id", "check_name", "severity"]]
    fl["customer_id"] = fl["customer_id"].astype("Int64")
    copy_dataframe(conn, fl, "audit.dq_row_flags", truncate=True)


def load_staging(conn: psycopg.Connection, clean: pd.DataFrame, run_id: int) -> int:
    mapping = staging_column_map()
    stg = clean[list(mapping)].rename(columns=mapping)
    int_cols = [c for c in stg.columns if not c.startswith(("bill_", "payment_", "credit_limit"))]
    stg[int_cols] = stg[int_cols].astype("int64")
    stg["load_run_id"] = run_id
    return copy_dataframe(conn, stg, "staging.stg_credit_card_clients")


def load_rejected(conn: psycopg.Connection, rejected: pd.DataFrame, run_id: int) -> int:
    if rejected.empty:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE staging.rejected_rows")
        conn.commit()
        return 0
    out = pd.DataFrame({
        "source_row_number": rejected["source_row_number"].astype(int),
        "raw_record": rejected.drop(columns=["reject_reasons"]).apply(
            lambda r: json.dumps({k: (None if pd.isna(v) else v) for k, v in r.items()
                                  if k != "source_row_number"}, default=str), axis=1),
        "reject_reasons": rejected["reject_reasons"],
        "load_run_id": run_id,
    })
    return copy_dataframe(conn, out, "staging.rejected_rows")
