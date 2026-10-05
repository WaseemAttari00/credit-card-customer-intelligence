"""Data-quality checks for the raw credit card file.

Each check returns a boolean Series (True = row fails) or, for dataset-level checks,
a single bool. Severity decides what happens to failing rows:

  ERROR -> the row is quarantined (not loaded into staging)
  WARN  -> the row is loaded but flagged in audit.dq_row_flags
  INFO  -> recorded for documentation only

The checks run on the raw, text-typed DataFrame produced by src.ingestion.extract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from src.ingestion.extract import EXPECTED_COLUMNS

STATUS_COLS = ["PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6"]  # Sep ... Apr
BILL_COLS = [f"BILL_AMT{i}" for i in range(1, 7)]                   # Sep ... Apr
PAYAMT_COLS = [f"PAY_AMT{i}" for i in range(1, 7)]                  # Sep ... Apr
NUMERIC_COLS = [c for c in EXPECTED_COLUMNS]
TARGET_COL = "default payment next month"


@dataclass
class Check:
    name: str
    severity: str  # ERROR / WARN / INFO
    description: str
    func: Callable[[pd.DataFrame, dict], "pd.Series | bool"]
    row_level: bool = True


def to_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Numeric view of the raw frame. Unparseable values become NaN (caught by a check)."""
    out = df.copy()
    for c in NUMERIC_COLS:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


# ---------------------------------------------------------------- row-level checks

def _non_numeric(raw: pd.DataFrame, num: pd.DataFrame) -> pd.Series:
    # value present in the raw text but could not be parsed as a number
    bad = pd.Series(False, index=raw.index)
    for c in NUMERIC_COLS:
        bad |= raw[c].notna() & num[c].isna()
    return bad


def _non_integer(num: pd.DataFrame) -> pd.Series:
    vals = num[NUMERIC_COLS]
    return ((vals.round() - vals).abs() > 1e-9).any(axis=1)


def build_checks() -> list[Check]:
    return [
        Check("non_numeric_value", "ERROR", "A numeric field contains text that cannot be parsed",
              lambda d, c: _non_numeric(d["raw"], d["num"])),
        Check("missing_value", "ERROR", "A required field is null",
              lambda d, c: d["raw"][NUMERIC_COLS].isna().any(axis=1)),
        Check("non_integer_value", "ERROR", "Amounts and codes are integers in this source; a decimal means a corrupt value",
              lambda d, c: _non_integer(d["num"])),
        Check("duplicate_customer_id", "ERROR", "Customer ID appears more than once",
              lambda d, c: d["num"]["ID"].duplicated(keep=False) & d["num"]["ID"].notna()),
        Check("invalid_customer_id", "ERROR", "Customer ID is not a positive integer",
              lambda d, c: ~(d["num"]["ID"] > 0)),
        Check("invalid_sex_code", "ERROR", "SEX not in documented codes {1,2}",
              lambda d, c: ~d["num"]["SEX"].isin(c["documented_codes"]["sex"])),
        Check("age_out_of_range", "ERROR", "AGE outside plausible range for a cardholder",
              lambda d, c: ~d["num"]["AGE"].between(c["age_min"], c["age_max"])),
        Check("non_positive_limit", "ERROR", "Credit limit must be > 0",
              lambda d, c: ~(d["num"]["LIMIT_BAL"] > 0)),
        Check("status_out_of_range", "ERROR", "Repayment status outside [-2, 9]",
              lambda d, c: ~d["num"][STATUS_COLS].apply(
                  lambda s: s.between(*c["status_range"])).all(axis=1)),
        Check("negative_payment", "ERROR", "Payment amount below zero",
              lambda d, c: (d["num"][PAYAMT_COLS] < 0).any(axis=1)),
        Check("invalid_target", "ERROR", "Default label not in {0,1}",
              lambda d, c: ~d["num"][TARGET_COL].isin([0, 1])),
        # --- warnings: loaded, but flagged
        Check("undocumented_education_code", "WARN", "EDUCATION is 0, 5 or 6 (not in the data documentation); mapped to 'Unknown'",
              lambda d, c: ~d["num"]["EDUCATION"].isin(c["documented_codes"]["education"])),
        Check("undocumented_marriage_code", "WARN", "MARRIAGE is 0 (not documented); mapped to 'Unknown'",
              lambda d, c: ~d["num"]["MARRIAGE"].isin(c["documented_codes"]["marriage"])),
        Check("credit_balance", "WARN", "Negative bill amount in at least one month (customer overpaid; plausible)",
              lambda d, c: (d["num"][BILL_COLS] < 0).any(axis=1)),
        Check("over_limit_balance", "WARN", "Bill above the credit limit in at least one month (plausible with interest/fees)",
              lambda d, c: d["num"][BILL_COLS].gt(d["num"]["LIMIT_BAL"], axis=0).any(axis=1)),
        Check("extreme_over_limit", "WARN", "Bill above 1.5x the credit limit (unusual; kept but worth a look)",
              lambda d, c: d["num"][BILL_COLS].gt(1.5 * d["num"]["LIMIT_BAL"], axis=0).any(axis=1)),
        Check("payment_far_above_limit", "WARN", "A single monthly payment above max_payment_to_limit x credit limit",
              lambda d, c: d["num"][PAYAMT_COLS].gt(c["max_payment_to_limit"] * d["num"]["LIMIT_BAL"], axis=0).any(axis=1)),
        Check("duplicate_profile", "WARN", "Identical to another row on every field except ID (mostly never-used accounts; kept as separate customers)",
              lambda d, c: d["num"].drop(columns=["ID", "source_row_number"]).duplicated(keep=False)),
        Check("delinquent_with_no_balance", "WARN", "Status says payment is late but the bill that month is <= 0",
              lambda d, c: pd.concat([(d["num"][s] >= 1) & (d["num"][b] <= 0)
                                      for s, b in zip(STATUS_COLS, BILL_COLS)], axis=1).any(axis=1)),
        Check("balance_drop_exceeds_payment", "WARN", "Bill fell by more than the payment received (refund, reversal or write-off)",
              lambda d, c: pd.concat([(d["num"][BILL_COLS[i]] - d["num"][BILL_COLS[i + 1]] + d["num"][PAYAMT_COLS[i]]) < 0
                                      for i in range(5)], axis=1).any(axis=1)),
        Check("never_used_account", "INFO", "Zero bills and zero payments in all six months",
              lambda d, c: (d["num"][BILL_COLS + PAYAMT_COLS] == 0).all(axis=1)),
        # --- dataset-level checks
        Check("schema_columns", "ERROR", "All expected source columns are present",
              lambda d, c: not set(EXPECTED_COLUMNS).issubset(d["raw"].columns), row_level=False),
        Check("file_checksum", "WARN", "File hash differs from the one recorded in config (source may have changed)",
              lambda d, c: d["meta"]["file_sha256"] != d["expected_sha256"], row_level=False),
        Check("status_coding_shift_latest_month", "WARN",
              "Status 1 ('one month late') is used >10x more often in Sep than in any earlier month; "
              "earlier months jump 0 -> 2. Treat Sep status codes as not comparable with Apr-Aug.",
              lambda d, c: (d["num"]["PAY_0"] == 1).sum() > 10 * max((d["num"][s] == 1).sum() for s in STATUS_COLS[1:]),
              row_level=False),
    ]


def run_checks(raw: pd.DataFrame, meta: dict, cfg: dict, expected_sha256: str | None = None):
    """Run every check. Returns (results table, row flags in long format, clean numeric rows, rejected rows)."""
    checks = build_checks()
    schema_check = next(c for c in checks if c.name == "schema_columns")
    data = {"raw": raw, "meta": meta, "expected_sha256": expected_sha256 or meta.get("file_sha256")}
    if schema_check.func(data, cfg):
        missing = sorted(set(EXPECTED_COLUMNS) - set(raw.columns))
        raise ValueError(f"Source file is missing expected columns: {missing}")
    data["num"] = to_numeric(raw)

    results, flags = [], []
    error_mask = pd.Series(False, index=raw.index)
    for chk in checks:
        failed = chk.func(data, cfg)
        if chk.row_level:
            failed = failed.fillna(True).astype(bool)
            n_failed = int(failed.sum())
            ids = data["num"].loc[failed, "ID"]
            if n_failed:
                flags.append(pd.DataFrame({"source_row_number": raw.loc[failed, "source_row_number"].astype(int),
                                           "customer_id": ids, "check_name": chk.name, "severity": chk.severity}))
            if chk.severity == "ERROR":
                error_mask |= failed
            sample = ", ".join(str(int(i)) for i in ids.dropna().head(5))
        else:
            n_failed = int(bool(failed))
            sample = ""
        results.append({"check_name": chk.name, "severity": chk.severity, "level": "row" if chk.row_level else "dataset",
                        "description": chk.description, "rows_failed": n_failed,
                        "pct_failed": round(100 * n_failed / len(raw), 3) if chk.row_level else np.nan,
                        "passed": n_failed == 0, "sample_customer_ids": sample})

    results = pd.DataFrame(results)
    flags = pd.concat(flags, ignore_index=True) if flags else pd.DataFrame(
        columns=["source_row_number", "customer_id", "check_name", "severity"])
    clean = data["num"].loc[~error_mask].copy()
    rejected = raw.loc[error_mask].copy()
    if len(rejected):
        reasons = flags[flags.severity == "ERROR"].groupby("source_row_number")["check_name"].agg("; ".join)
        rejected["reject_reasons"] = rejected["source_row_number"].map(reasons)
    return results, flags, clean, rejected
