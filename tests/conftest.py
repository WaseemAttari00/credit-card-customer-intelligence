import pytest

from src.db import connect, read_sql


@pytest.fixture(scope="session")
def db():
    """Live DB connection. Tests that need the built warehouse are skipped if it isn't there."""
    try:
        conn = connect()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"database not reachable: {exc}")
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def q(db):
    def _q(sql, params=None):
        return read_sql(sql, db, params)
    return _q


def table_exists(q, name: str) -> bool:
    return bool(q("SELECT to_regclass(%s) IS NOT NULL AS ok", (name,)).ok[0])


@pytest.fixture(scope="session")
def require(q):
    def _require(*tables):
        missing = [t for t in tables if not table_exists(q, t)]
        if missing:
            pytest.skip(f"run the pipeline first; missing {missing}")
    return _require
