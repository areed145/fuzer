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
