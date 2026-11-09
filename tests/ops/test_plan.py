"""The integrated plan (core/plan.py, ops/plan.py) on an empty database with a small plant."""

import os
import tempfile

import pytest
from psycopg.types.json import Jsonb

from fuzer.core import db, events
from fuzer.core.plan import PlanError, propose, update
from fuzer.ops import plan

T = "2026-11-10T08:00:00+00:00"


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        c.execute("insert into entity (fuzer_id, kind) values ('fz-area-north', 'area'), ('fz-pump-1', 'equipment'), "
                  "('fz-pump-2', 'equipment'), ('fz-wo-1', 'work_order'), ('fz-wo-2', 'work_order'), "
                  "('fz-wo-3', 'work_order')")
        c.execute("insert into entity_label (fuzer_id, label) values ('fz-area-north', 'North'), "
                  "('fz-pump-1', 'P-101') on conflict do nothing")
        for src in ("fz-pump-1", "fz-pump-2"):
            c.execute("insert into edge (src, rel, dst, sources, confidence) values (%s, 'part_of', 'fz-area-north', "
                      "'{t}', 1)", (src,))
        c.execute("insert into entity_key (kind, key, fuzer_id) values ('tag', 'P-101', 'fz-pump-1'), "
                  "('work_order', '4000001', 'fz-wo-1'), ('work_order', '4000002', 'fz-wo-2'), "
                  "('work_order', '4000003', 'fz-wo-3')")
        c.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-wo-1', 'concerns', 'fz-pump-1', "
                  "'{t}', 1), ('fz-wo-2', 'concerns', 'fz-pump-2', '{t}', 1)")
        golden = {"fz-wo-1": {"text": "Replace P-101 seal", "start": "2026-11-12", "finish": "2026-11-13", "status": "REL"},
                  "fz-wo-2": {"text": "North train overhaul", "start": "2026-11-20", "finish": "2026-11-25",
                              "status": "CRTD", "revision": "TA26"},
                  "fz-wo-3": {"text": "Old order", "basic_finish": "2026-11-01", "status": "TECO"}}
        for fid, fields in golden.items():
            for k, v in fields.items():
                c.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, %s, %s, 'sap', 't')",
                          (fid, k, Jsonb(v)))
        c.commit()
        yield c


def test_items_are_matched_to_assets_and_their_area(conn):
    p = propose(conn, "inspection", "Inspect P-101", "ann", T, "2026-11-10T12:00:00+00:00", refs=["tag:P-101"])
    assert p["subjects"] == ["fz-pump-1"] and p["area_id"] == "fz-area-north" and p["status"] == "proposed"
    assert any(e["ref"] == f"plan_item:{p['id']}" for e in events.pending(conn, "plan.changed"))
    with pytest.raises(PlanError, match="end before"):
        propose(conn, "inspection", "Backwards", "ann", "2026-11-10", "2026-11-09")
    with pytest.raises(PlanError, match="kind must be"):
        propose(conn, "picnic", "No", "ann", T, T)
    conn.commit()


def test_sap_work_orders_become_plan_items_and_clashes_and_bundles_show(conn):
    out = plan.sync_work_orders(conn)
    assert out["added"] == 2 and out["skipped"] == 0
    again = plan.sync_work_orders(conn)
    assert again["added"] == 0 and again["updated"] == 2  # idempotent by order number
    rows = {r["title"]: r for r in plan.items(conn, "2026-11-01", "2026-12-31")}
    seal, overhaul = rows["Replace P-101 seal"], rows["North train overhaul"]
    assert seal["kind"] == "maintenance" and seal["status"] == "scheduled" and seal["source_key"] == "4000001"
    assert overhaul["kind"] == "turnaround" and overhaul["needs_shutdown"] and overhaul["area_id"] == "fz-area-north"
    assert set(seal["subjects"]) == {"fz-wo-1", "fz-pump-1"} and seal["area_label"] == "North"
    assert any(c["kind"] == "bundle" and c["with"] == overhaul["id"] for c in seal["clashes"])  # 8 days before it
    assert "Old order" not in rows

    clash = propose(conn, "maintenance", "Lube P-101", "bob", "2026-11-12T10:00:00+00:00",
                    "2026-11-12T11:00:00+00:00", refs=["fz-pump-1"], crew="fred")
    update(conn, seal["id"], "ann", crew="fred")
    rows = {r["id"]: r for r in plan.items(conn, "2026-11-01", "2026-12-31")}
    kinds = {c["kind"] for c in rows[clash["id"]]["clashes"]}
    assert {"same_asset", "crew"} <= kinds
    update(conn, clash["id"], "ann", status="cancelled")
    rows = {r["id"]: r for r in plan.items(conn, "2026-11-01", "2026-12-31")}
    assert not [c for c in rows[seal["id"]]["clashes"] if c["kind"] != "bundle"]
    conn.commit()


def test_a_closed_order_leaves_the_plan_and_people_choices_stick(conn):
    seal = conn.execute("select * from plan_item where source_key = '4000001'").fetchone()
    assert seal["crew"] == "fred"
    plan.sync_work_orders(conn)
    assert conn.execute("select crew from plan_item where id = %s", (seal["id"],)).fetchone()["crew"] == "fred"
    conn.execute("update golden set value = '\"TECO\"' where fuzer_id = 'fz-wo-1' and field = 'status'")
    assert plan.sync_work_orders(conn)["closed"] == 1
    assert conn.execute("select status from plan_item where id = %s", (seal["id"],)).fetchone()["status"] == "done"
    conn.rollback()


def test_an_exception_can_be_planned_instead_of_worked_now(conn, monkeypatch):
    from fuzer.core.exceptions import TYPES, ExceptionType, raise_exception

    monkeypatch.setitem(TYPES, "minor_leak", ExceptionType("minor_leak", "Minor leak"))
    e = raise_exception(conn, "minor_leak", "P-101 drip", "P-101 drip", "olga", refs=["tag:P-101"], value_usd=300)
    item = plan.plan_exception(conn, e["id"], "ann", "2026-11-21", "2026-11-21T04:00:00+00:00")
    assert item["exception_id"] == e["id"] and item["subjects"] == ["fz-pump-1"] and item["status"] == "scheduled"
    assert item["deferment_usd"] == 300
    with pytest.raises(PlanError, match="no exception"):
        plan.plan_exception(conn, 99999, "ann", T, T)
    conn.rollback()
