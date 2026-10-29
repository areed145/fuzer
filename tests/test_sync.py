"""db.sync_table applies only the difference between a table and the rows it should hold."""

import os
import tempfile

import pytest

from fuzer import db


@pytest.fixture()
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        c.execute("create temp table t (k text, d date, v text)")
        yield c
        c.rollback()


def rows(conn):
    return sorted((r["k"], r["d"] and r["d"].isoformat(), r["v"]) for r in conn.execute("select * from t"))


def test_insert_update_delete_and_null_keys(conn):
    first = db.sync_table(conn, "t", ["k", "d"], ["v"], [("a", None, "1"), ("b", "2024-01-01", "1")], nullable_keys=("d",))
    assert first == {"rows": 2, "inserted": 2, "updated": 0, "deleted": 0}
    again = db.sync_table(conn, "t", ["k", "d"], ["v"], [("a", None, "1"), ("b", "2024-01-01", "1")], nullable_keys=("d",))
    assert again == {"rows": 2, "inserted": 0, "updated": 0, "deleted": 0}  # nothing changed, nothing written
    change = db.sync_table(conn, "t", ["k", "d"], ["v"], [("a", None, "2"), ("c", None, "1")], nullable_keys=("d",))
    assert change == {"rows": 2, "inserted": 1, "updated": 1, "deleted": 1}
    assert rows(conn) == [("a", None, "2"), ("c", None, "1")]
