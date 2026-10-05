"""Extract -> validate -> load. Writes raw, staging, quarantine and audit tables."""
from __future__ import annotations

import logging

from src.config import load_config, path
from src.db import connect, run_sql_file
from src.ingestion import load
from src.ingestion.extract import extract
from src.validation.checks import run_checks

log = logging.getLogger(__name__)


class DataQualityError(RuntimeError):
    pass


def run() -> dict:
    cfg = load_config()
    raw, meta = extract()
    with connect() as conn:
        run_sql_file(conn, path("sql/staging/00_schemas_and_tables.sql"))
        run_id = load.start_run(conn, meta)
        try:
            load.load_raw(conn, raw)
            results, flags, clean, rejected = run_checks(raw, meta, cfg["validation"], cfg["data"]["raw_sha256"])
            load.write_dq_results(conn, run_id, results, flags)

            reject_share = len(rejected) / len(raw)
            if reject_share > cfg["validation"]["max_error_row_share"]:
                raise DataQualityError(
                    f"{len(rejected)} rows ({reject_share:.1%}) failed ERROR checks; "
                    f"threshold is {cfg['validation']['max_error_row_share']:.1%}. See audit.dq_check_results.")

            n_loaded = load.load_staging(conn, clean, run_id)
            n_rejected = load.load_rejected(conn, rejected, run_id)
            load.finish_run(conn, run_id, "loaded", n_loaded, n_rejected)
        except Exception as exc:
            conn.rollback()
            load.finish_run(conn, run_id, "failed", notes=str(exc)[:500])
            raise
    failed = results[~results.passed]
    log.info("run %s: loaded %s rows, rejected %s; %s checks raised issues",
             run_id, n_loaded, n_rejected, len(failed))
    return {"run_id": run_id, "loaded": n_loaded, "rejected": n_rejected, "dq_results": results}
