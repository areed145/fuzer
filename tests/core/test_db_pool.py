"""The API server's connection pool: a closed connection is reused by the next connect(), as new as the first."""

import psycopg
import pytest

from fuzer.core import db


@pytest.fixture
def pooled():
    with db.connect() as c:
        db.init_schema(c)
    db.pool(2)
    yield
    db.pool(0)


def _pid(conn) -> int:
    return conn.execute("select pg_backend_pid() as pid").fetchone()["pid"]


def test_a_closed_connection_is_reused_with_its_session_cleared(pooled):
    with db.connect() as c:
        first = _pid(c)
        path = c.execute("show search_path").fetchone()["search_path"]
        c.execute("set statement_timeout = '5s'")
        c.execute("create temp table pool_scratch (x int)")
        c.execute("select pg_advisory_lock(42)")
        c.commit()
        c.read_only = True  # a client-side setting, as the read-only journeys make
    with db.connect(autocommit=True) as c:
        assert _pid(c) == first and c.autocommit
        assert c.execute("show statement_timeout").fetchone()["statement_timeout"] == "0"
        assert c.execute("select to_regclass('pool_scratch') as t").fetchone()["t"] is None
        assert c.execute("show search_path").fetchone()["search_path"] == path  # as connect() opened it
        assert not c.execute("select 1 from pg_locks where locktype = 'advisory' and pid = %s", (first,)).fetchone()
    with db.connect() as c:
        assert _pid(c) == first and not c.autocommit
        c.execute("create temp table pool_writes (x int)")  # not read-only any more


def test_a_failed_transaction_and_uncommitted_work_do_not_reach_the_next_user(pooled):
    c = db.connect()
    c.execute("create table pool_uncommitted (x int)")
    with pytest.raises(psycopg.Error):
        c.execute("select * from no_such_table")
    c.close()  # rolled back, then kept
    with db.connect() as c:
        assert c.execute("select to_regclass('pool_uncommitted') as t").fetchone()["t"] is None


def test_each_project_schema_has_its_own_connections_and_the_pool_is_bounded(pooled):
    with db.connect() as a, db.connect(schema="fuzer") as b:
        assert _pid(a) != _pid(b)
        assert b.execute("show search_path").fetchone()["search_path"].startswith("fuzer")
    held = [db.connect() for _ in range(4)]
    pids = {_pid(c) for c in held}
    for c in held:
        c.close()
    assert len(db._pool[held[0]._fuzer_key]) == 2  # two kept, two really closed
    assert sum(c.closed for c in held) == 2
    with db.connect() as c:
        assert _pid(c) in pids


def test_an_idle_connection_past_its_age_is_not_reused(pooled, monkeypatch):
    with db.connect() as c:
        first = _pid(c)
    monkeypatch.setattr(db, "POOL_IDLE_SECONDS", -1)
    with db.connect() as c:
        assert _pid(c) != first


def test_without_a_pool_every_close_ends_the_connection():
    db.pool(0)
    c = db.connect()
    c.close()
    assert c.closed and type(c) is psycopg.Connection
