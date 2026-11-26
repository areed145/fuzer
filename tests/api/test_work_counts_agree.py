"""One count per measure (#1352): open, overdue and what waits on a person are counted once, over the List's own rows
(ops/worklist.figures), and every endpoint that shows one of them gives the same number for the same scope: the Work
badge, Home and My work (`/work/counts`), All work (`/cases`), a report's case lists (`within` its scope) and Home's
map (`/attention`, which reads the List's definition of overdue)."""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.sequence


def test_the_counts_agree_across_every_endpoint_that_shows_them(okan_db):
    from fuzer import api
    from fuzer.workflow import engine

    conn = okan_db.conn
    roles = [r["name"] for r in conn.execute("select name from work_role where kind = 'person'")]
    engine.set_person(conn, "ivy", roles)
    # some of the open work is past its due time
    conn.execute("update work_item set due = now() - interval '2 days' where id in "
                 "(select id from work_item where closed_at is null order by id limit 7)")
    conn.commit()
    with TestClient(api.app) as client:
        c = client.get("/work/counts", params={"person": "ivy"}).json()
        mine = c["waiting_on_you"]
        assert c["overdue"] >= 7 and mine["total"] and mine["my_work"] == mine["total"] - mine["to_dispatch"]
        # My work's chips (P5.28d): Assigned to you and Unclaimed in your roles add up to its All
        assert mine["assigned"] + mine["unclaimed"] == mine["my_work"]

        def total(**f):
            return client.get("/cases", params={"limit": 5000, **f}).json()

        everything = total()
        assert everything["total"] == c["open"] and everything["facets"]["overdue"] == c["overdue"]
        assert total(overdue="true")["total"] == c["overdue"]
        waiting = total(waiting_on="ivy")
        assert waiting["total"] == mine["total"] and waiting["facets"]["overdue"] == mine["overdue"]
        assert total(waiting_on="ivy", stage="desk,planned,field,back")["total"] == mine["my_work"]

        # a report's list is the same rows within its scope
        fac = client.get("/reports/work").json()["scope"]["id"]
        late = client.get("/reports/work/panels/overdue", params={"scope": fac}).json()
        assert late["total"] == total(within=fac, overdue="true")["total"] and late["scope"]["id"] == fac

        # Home's map reads the List's definition of overdue (worklist.dues): a case is overdue there exactly when the
        # List says so, and a place's items agree with the List's rows
        from fuzer.ops import worklist

        rows = [r for r in everything["rows"] if r.get("item_id")]
        dues = worklist.dues(conn, [r["item_id"] for r in rows])
        assert all(dues[r["item_id"]]["overdue"] == r["overdue"] for r in rows)
        assert sum(d["overdue"] for d in dues.values()) >= 7
        overdue = {r["exception_id"]: r["overdue"] for r in rows if r.get("exception_id")}
        spots = client.get("/attention").json()["places"]
        assert all(i["overdue"] == overdue[i["id"]] for p in spots for i in p["top"] if i["id"] in overdue)


def test_the_counts_follow_a_change_at_once(okan_db):
    """P5.26h, #1370: the counts the badge and Tower read and All work's list are one computation shared until anything
    is written. Work cleared is gone from both on the next read, and they still agree: no count is served from before
    the change."""
    from fuzer import api
    from fuzer.ops import exceptions
    from fuzer.workflow import engine

    conn = okan_db.conn
    engine.set_person(conn, "ivy", [r["name"] for r in conn.execute("select name from work_role where kind = 'person'")])
    conn.commit()
    with TestClient(api.app) as client:
        before = client.get("/work/counts", params={"person": "ivy"}).json()
        rows = client.get("/cases", params={"limit": 5000}).json()
        assert rows["total"] == before["open"]
        mine = {r.get("item_id") for r in client.get("/cases", params={"limit": 5000, "waiting_on": "ivy"}).json()["rows"]}
        row = next(r for r in rows["rows"] if r.get("item_id") and r.get("exception_id") and r["item_id"] in mine)
        item = row["item_id"]
        ex = conn.execute("select type, key from exception_record where id = %s", (row["exception_id"],)).fetchone()
        assert exceptions.clear(conn, ex["type"], ex["key"], "ivy", "cleared in the test")
        conn.commit()
        after = client.get("/work/counts", params={"person": "ivy"}).json()
        listed = client.get("/cases", params={"limit": 5000}).json()
        assert after["open"] == before["open"] - 1 == listed["total"]
        assert after["waiting_on_you"]["total"] == before["waiting_on_you"]["total"] - 1
        assert item not in {r.get("item_id") for r in listed["rows"]}


def test_a_write_is_committed_before_its_answer_is_sent(okan_db, monkeypatch):
    """P5.28d, #1392: the request's connection committed after the answer had gone, so the reads the page made at once
    (and on the 1-CPU server those made seconds later) still showed the case with its old owner until a reload. A
    route's connection now commits before the answer starts."""
    import asyncio
    import sys

    from fuzer import api
    from fuzer.api import deps

    said: list[str] = []
    real = deps.db.connect

    class Recorded:  # the route's connection, saying when what it wrote is committed
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            self.inner = self.conn.__enter__()
            real_commit = self.inner.commit
            self.inner.commit = lambda: (said.append("committed"), real_commit())[1]
            return self.inner

        def __exit__(self, *exc):
            return self.conn.__exit__(*exc)

    def connect(*a, **k):
        conn = real(*a, **k)
        return Recorded(conn) if sys._getframe(1).f_code.co_name == "get_conn" else conn

    monkeypatch.setattr(deps.db, "connect", connect)
    with TestClient(api.app):  # the app's startup; then one request by hand, to see the order of what happens
        scope = {"type": "http", "method": "GET", "path": "/work/people", "raw_path": b"/work/people",
                 "query_string": b"", "headers": [(b"host", b"testserver")], "scheme": "http",
                 "server": ("testserver", 80), "client": ("127.0.0.1", 1), "root_path": "", "http_version": "1.1",
                 "app": api.app}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                said.append(f"answered {message['status']}")

        asyncio.run(api.app(scope, receive, send))
    # the connection's set-up commits once; the route's own work is committed next, before the answer, not after it
    assert said[:3] == ["committed", "committed", "answered 200"], said


def test_take_it_moves_a_case_from_unclaimed_to_assigned(okan_db):
    """P5.28d, #1392 (B-N15): after Take it, My work's Assigned to you stayed 0. The chips are the server's counts now,
    read with the badge's, so taking one moves it from Unclaimed to Assigned and All stays the same."""
    from fuzer import api
    from fuzer.ops import worklist
    from fuzer.workflow import engine

    conn = okan_db.conn
    engine.set_person(conn, "ivy", [r["name"] for r in conn.execute("select name from work_role where kind = 'person'")])
    conn.commit()
    with TestClient(api.app) as client:
        before = client.get("/work/counts", params={"person": "ivy"}).json()["waiting_on_you"]
        rows, _ = worklist._shared(conn, "open")
        row = next(r for r in rows if r.get("item_id") and r["stage"] != "dispatch" and len(r["waiting"] or []) == 1
                   and not r["waiting"][0].get("assignee"))
        engine.take_item(conn, row["item_id"], "ivy")
        conn.commit()
        after = client.get("/work/counts", params={"person": "ivy"}).json()["waiting_on_you"]
    assert after["assigned"] == before["assigned"] + 1 and after["unclaimed"] == before["unclaimed"] - 1
    assert after["my_work"] == before["my_work"] == after["assigned"] + after["unclaimed"]
