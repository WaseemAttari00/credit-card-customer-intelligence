"""Extraction: get the raw UCI file onto disk (if needed), verify it, and read it unchanged."""
from __future__ import annotations

import hashlib
import io
import logging
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

from src.config import load_config, path

log = logging.getLogger(__name__)

# Source column names, in file order (the .xls has a two-row header; row 2 holds these names)
EXPECTED_COLUMNS = (
    ["ID", "LIMIT_BAL", "SEX", "EDUCATION", "MARRIAGE", "AGE"]
    + ["PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6"]
    + [f"BILL_AMT{i}" for i in range(1, 7)]
    + [f"PAY_AMT{i}" for i in range(1, 7)]
    + ["default payment next month"]
)


def sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_raw_file() -> Path:
    """Download and unzip the dataset if it is not already in data/raw/."""
    cfg = load_config()["data"]
    raw_path = path(cfg["raw_file"])
    if raw_path.exists():
        return raw_path
    log.info("downloading %s", cfg["source_url"])
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(cfg["source_url"], timeout=120) as resp:
        payload = resp.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        member = next(n for n in zf.namelist() if n.endswith(".xls"))
        raw_path.write_bytes(zf.read(member))
    return raw_path


def read_raw(raw_path: Path) -> pd.DataFrame:
    """Read the file exactly as delivered, as text, so that type problems are caught by
    validation instead of silently coerced by the reader."""
    df = pd.read_excel(raw_path, header=1, dtype=str)
    df.insert(0, "source_row_number", range(3, len(df) + 3))  # Excel row (2 header rows)
    return df


def extract() -> tuple[pd.DataFrame, dict]:
    raw_path = ensure_raw_file()
    file_hash = sha256(raw_path)
    df = read_raw(raw_path)
    meta = {"source_file": raw_path.name, "file_sha256": file_hash, "rows_read": len(df)}
    log.info("extracted %s rows from %s", len(df), raw_path.name)
    return df, meta
