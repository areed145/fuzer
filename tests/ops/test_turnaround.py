"""Turnarounds (ops/turnaround.py): a revision's scope read against the integrated plan."""

import os
import tempfile

import pytest
from psycopg.types.json import Jsonb

from fuzer.core import db
from fuzer.core.plan import PlanError, propose
from fuzer.ops import plan, turnaround


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        c.execute("insert into entity (fuzer_id, kind) values ('fz-area-north', 'area'), ('fz-pump-1', 'equipment'), "
                  "('fz-pump-2', 'equipment'), ('fz-wo-1', 'work_order')")
        for src in ("fz-pump-1", "fz-pump-2"):
            c.execute("insert into edge (src, rel, dst, sources, confidence) values (%s, 'part_of', 'fz-area-north', "
                      "'{t}', 1)", (src,))
        c.execute("insert into entity_key (kind, key, fuzer_id) values ('work_order', '4000002', 'fz-wo-1')")
        c.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-wo-1', 'concerns', 'fz-pump-1', "
                  "'{t}', 1)")
        for k, v in {"text": "North train overhaul", "start": "2026-11-20", "finish": "2026-11-25", "status": "CRTD",
                     "revision": "TA26"}.items():
            c.execute("insert into golden (fuzer_id, field, value, source, rule) values ('fz-wo-1', %s, %s, 'sap', 't')",
                      (k, Jsonb(v)))
        plan.sync_work_orders(c)
        c.commit()
        yield c


def test_scope_shows_the_window_what_could_ride_along_and_shutdown_work_outside_it(conn):
    near = propose(conn, "maintenance", "Repack P-102", "ann", "2026-11-10", "2026-11-11", refs=["fz-pump-2"])
    away = propose(conn, "inspection", "PSV test", "ann", "2027-03-01", "2027-03-02", refs=["fz-pump-2"],
                   needs_shutdown=True)
    s = turnaround.scope(conn, "TA26")
    assert s["revision"] == "TA26" and [w["id"] for w in s["scoped_work"]] == ["fz-wo-1"]
    assert s["window"]["start"].date().isoformat() == "2026-11-20" and len(s["on_plan"]) == 1
    assert [i["id"] for i in s["ride_along"]] == [near["id"]]
    assert [i["id"] for i in s["shutdown_outside"]] == [away["id"]]

    moved = turnaround.bring_in(conn, away["id"], "TA26", "ann")
    assert moved["start_at"] == s["window"]["start"] and "in turnaround TA26" in moved["notes"]
    assert moved["moved_by"] == "ann"
    assert not turnaround.scope(conn, "TA26")["shutdown_outside"]
    with pytest.raises(PlanError, match="no plan item"):
        turnaround.bring_in(conn, 99999, "TA26", "ann")
    conn.rollback()


def test_a_sources_next_proposal_keeps_dates_a_person_moved(conn):
    item = conn.execute("select * from plan_item where source_key = '4000002'").fetchone()
    plan.update(conn, item["id"], "ann", start="2026-11-21", end="2026-11-26")
    plan.sync_work_orders(conn)
    again = conn.execute("select * from plan_item where id = %s", (item["id"],)).fetchone()
    assert again["start_at"].date().isoformat() == "2026-11-21"  # SAP still says the 20th; the person's date wins
    conn.rollback()
