"""One container (step 6 of docs/work-model.md): every level work is grouped at, with a parent and a governing case."""

import os
import tempfile

import pytest

from fuzer.core import db, work
from fuzer.core.plan import propose
from fuzer.ops import containers


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        c.execute("insert into entity (fuzer_id, kind) values ('fz-pump-1', 'equipment')")
        c.commit()
        yield c


def test_a_container_has_a_parent_and_a_governing_case(conn):
    bp = containers.create(conn, "business_plan", "2027 business plan", "ann", owner="olga")
    ta = containers.create(conn, "turnaround", "North train 2027", "ann", parent=bp["id"])
    pkg = containers.create(conn, "package", "Compressor package", "ann", parent=ta["id"], governed=False)
    assert bp["item_id"] and conn.execute("select assignee, type from work_item where id = %s",
                                          (bp["item_id"],)).fetchone() == {"assignee": "olga", "type": "container"}
    assert pkg["item_id"] is None and containers.govern(conn, pkg["id"], "ann")["item_id"]
    with pytest.raises(containers.ContainerError, match="inside itself"):
        containers.move(conn, bp["id"], "ann", pkg["id"])
    with pytest.raises(containers.ContainerError, match="kind must be"):
        containers.create(conn, "galaxy", "x", "ann")
    case = work.open_case(conn, "task", "Swap the seal", "ann")
    out = containers.add_case(conn, pkg["id"], case["id"], "ann")
    assert [c["id"] for c in out["cases"]] == [case["id"]] and [p["title"] for p in out["parents"]] == [
        "2027 business plan", "North train 2027"]
    assert containers.get(conn, ta["id"])["children"][0]["title"] == "Compressor package"
    listed = {c["id"]: c for c in containers.listing(conn)}
    assert listed[bp["id"]]["children"] == [ta["id"]] and listed[pkg["id"]]["cases"] == 1
    conn.rollback()


def test_schedules_and_sap_revisions_become_containers_with_their_jobs(conn):
    conn.execute("insert into plan_project (code, title, kind, start_at, created_by, updated_by) values "
                 "('OK-TAR-2027', 'Okan turnaround 2027', 'turnaround', '2027-03-01', 't', 't')")
    a = propose(conn, "turnaround", "Open the drum", "t", "2027-03-02T00:00:00+00:00", "2027-03-03T00:00:00+00:00")
    conn.execute("update plan_item set project = 'OK-TAR-2027' where id = %s", (a["id"],))
    b = propose(conn, "turnaround", "Inspect V-101", "sap", "2027-03-04T00:00:00+00:00", "2027-03-05T00:00:00+00:00",
                source="sap", source_key="4000123", notes="SAP order 4000123, revision TA27")
    out = containers.sync(conn)
    assert out["jobs_placed"] == 2
    found = {c["code"]: c for c in containers.listing(conn)}
    assert found["schedule:OK-TAR-2027"]["kind"] == "turnaround" and found["schedule:OK-TAR-2027"]["jobs"] == {"planned": 1}
    assert found["revision:TA27"]["title"] == "Turnaround TA27"
    assert conn.execute("select container_id from plan_item where id = %s", (b["id"],)).fetchone()["container_id"] == \
        found["revision:TA27"]["id"]
    assert containers.sync(conn)["jobs_placed"] == 0  # once
    conn.rollback()
