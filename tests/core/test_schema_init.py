"""init_schema runs schema.sql only when it changed (its migrations lock busy tables), and parallel callers
on a new schema take turns instead of deadlocking (#208 found four Dagster checks deadlocking here)."""

import threading

import pytest

from fuzer.core import db


@pytest.fixture(scope="module")
def url():
    with db.connect() as c:
        db.reset(c)
    return db.database_url()


def test_records_the_applied_schema(url):
    with db.connect(url=url) as c:
        assert c.execute("select hash from schema_version").fetchone()["hash"] == db.schema_hash()


def test_current_schema_takes_no_table_locks(url):
    """A reader mid-transaction on `assertion` must not block init_schema (schema.sql alters that table)."""
    with db.connect(url=url) as reader, db.connect(url=url) as other:
        reader.execute("select count(*) from assertion").fetchone()  # holds AccessShareLock until rollback
        other.execute("set lock_timeout = '2s'")
        db.init_schema(other)  # raised LockNotAvailable when it re-ran the ALTER TABLEs every time
        reader.rollback()


def test_changed_schema_is_applied_once_by_parallel_callers(url):
    with db.connect(url=url) as c:
        c.execute("update schema_version set hash = 'old'")
        c.commit()
    errors: list[BaseException] = []

    def connect_and_init():
        try:
            with db.connect(url=url) as c:
                c.execute("select count(*) from golden").fetchone()
                db.init_schema(c)
        except BaseException as e:  # noqa: BLE001 - collected and asserted below
            errors.append(e)

    threads = [threading.Thread(target=connect_and_init) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    with db.connect(url=url) as c:
        assert c.execute("select hash from schema_version").fetchone()["hash"] == db.schema_hash()


def test_reset_reapplies(url):
    with db.connect(url=url) as c:
        db.reset(c)
        assert c.execute("select to_regclass('public.entity') as t").fetchone()["t"] is not None
        assert c.execute("select hash from schema_version").fetchone()["hash"] == db.schema_hash()


def test_old_database_without_version_table(url):
    with db.connect(url=url) as c:
        c.execute("drop table schema_version")
        c.commit()
        db.init_schema(c)
        assert c.execute("select hash from schema_version").fetchone()["hash"] == db.schema_hash()
