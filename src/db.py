"""Small helpers around psycopg for running SQL files and moving DataFrames in/out of Postgres."""
from __future__ import annotations

import io
import logging
from pathlib import Path

import pandas as pd
import psycopg

from src.config import database_url

log = logging.getLogger(__name__)


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url())


def run_sql_file(conn: psycopg.Connection, sql_path: Path) -> None:
    """Execute one .sql file. Each file is written to be idempotent (drop/create)."""
    sql = Path(sql_path).read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    log.info("ran %s", Path(sql_path).as_posix())


def read_sql(query: str, conn: psycopg.Connection | None = None, params=None) -> pd.DataFrame:
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            cols = [d.name for d in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        if own:
            conn.close()
    # numeric columns come back as Decimal; convert to float for analysis
    for c in df.columns:
        if df[c].dtype == object:
            non_null = df[c].dropna()
            if len(non_null) and type(non_null.iloc[0]).__name__ == "Decimal":
                df[c] = df[c].astype(float)
    return df


def _pg_type(dtype) -> str:
    if pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(dtype):
        return "BIGINT"
    if pd.api.types.is_float_dtype(dtype):
        return "DOUBLE PRECISION"
    return "TEXT"


def replace_table(conn: psycopg.Connection, df: pd.DataFrame, table: str, primary_key: list[str] | None = None) -> int:
    """Drop and recreate `table` from the DataFrame's dtypes, then COPY the rows in."""
    cols = ", ".join(f"{c} {_pg_type(t)}" for c, t in df.dtypes.items())
    pk = f", PRIMARY KEY ({', '.join(primary_key)})" if primary_key else ""
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        cur.execute(f"CREATE TABLE {table} ({cols}{pk})")
    conn.commit()
    return copy_dataframe(conn, df, table, truncate=False)


def copy_dataframe(conn: psycopg.Connection, df: pd.DataFrame, table: str, truncate: bool = True) -> int:
    """Bulk load a DataFrame into an existing table with COPY (much faster than INSERTs).

    Truncate + load inside one transaction keeps the load idempotent: rerunning
    the pipeline replaces the table contents instead of appending duplicates.
    """
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)
    cols = ", ".join(df.columns)
    with conn.cursor() as cur:
        if truncate:
            cur.execute(f"TRUNCATE {table}")
        with cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '\\N')") as copy:
            while data := buf.read(1 << 20):
                copy.write(data)
    conn.commit()
    return len(df)
