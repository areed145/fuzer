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


def test_shared_tables_live_in_the_fuzer_schema(url):
    """Accounts, access, projects and settings are shared by every project in the instance; the register's tables
    are the project's (public while a database holds one project). Queries find both by unqualified name."""
    with db.connect(url=url) as c:
        where = {r["tablename"]: r["schemaname"] for r in c.execute(
            "select schemaname, tablename from pg_tables where schemaname in ('public', 'fuzer')")}
        for t in ("access_account", "access_role", "access_membership", "access_session", "access_login",
                  "access_event", "setting", "setting_history", "project"):
            assert where[t] == "fuzer", t
        assert where["entity"] == where["source_record"] == where["schema_version"] == "public"
        assert c.execute("select count(*) as n from access_role").fetchone()["n"] >= 0
        c.rollback()


def test_a_database_from_before_the_split_moves_shared_tables_in_place(url):
    """An existing register keeps its accounts, settings and projects: the tables move, nothing is copied."""
    with db.connect(url=url) as c:
        db.reset(c)
        c.execute("insert into access_account (id, tenant_id, object_id, display_name, enabled, owner) "
                  "values ('a1', 't', 'o', 'Ana', true, true)")
        c.execute("insert into setting (scope, key, value, updated_by) values ('global', 'demo.enabled', 'false', 'ana')")
        c.execute("insert into project (name, config, updated_by) values ('okan', '{}', 'ana')")
        c.execute("insert into setting_history (scope, key, new, by) values ('global', 'demo.enabled', 'false', 'ana')")
        # Put them back where an older release kept them.
        for t in ("access_account", "access_role", "access_membership", "access_session", "access_login",
                  "access_event", "setting", "setting_history", "project"):
            c.execute(f"alter table fuzer.{t} set schema public")
        c.execute("drop schema fuzer")
        c.execute("update schema_version set hash = 'old'")
        c.commit()
        db.init_schema(c)
        assert c.execute("select to_regclass('fuzer.access_account') as t").fetchone()["t"] is not None
        assert c.execute("select to_regclass('public.access_account') as t").fetchone()["t"] is None
        assert c.execute("select display_name from access_account").fetchone()["display_name"] == "Ana"
        assert c.execute("select value from setting where key = 'demo.enabled'").fetchone()["value"] is False
        assert c.execute("select name from project").fetchone()["name"] == "okan"
        c.execute("insert into setting_history (scope, key, new, by) values ('global', 'x', '1', 'ana')")  # sequence moved too
        c.rollback()
