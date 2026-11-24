"""Routines (step 7 of docs/work-model.md): a standing rule raises an obligation need, a case and a job when due."""

import os
import tempfile
from datetime import date, timedelta

import pytest
from psycopg.types.json import Jsonb

from fuzer.core import db, work
from fuzer.ops import plan, routines


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        from fuzer.workflow import engine

        engine.load_defaults(c)
        c.execute("insert into entity (fuzer_id, kind) values ('fz-pump-1', 'equipment'), ('fz-mp-1', 'maintenance_plan')")
        c.execute("insert into entity_key (kind, key, fuzer_id) values ('maintenance_plan', 'MP-77', 'fz-mp-1')")
        c.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-mp-1', 'concerns', 'fz-pump-1', "
                  "'{t}', 1)")
        c.commit()
        yield c


def test_a_routine_kept_here_raises_its_occurrence_once_and_rolls_forward_when_done(conn, monkeypatch):
    today = date(2026, 12, 1)
    monkeypatch.setattr(routines, "_today", lambda: today)
    r = routines.create(conn, "Sample the produced water", "ann", 7, today + timedelta(days=3), targets=["fz-pump-1"],
                        lead_days=5, owner_role="operator", template={"kind": "maintenance", "hours": 2})
    with pytest.raises(routines.RoutineError, match="anchor"):
        routines.create(conn, "x", "ann", 7, today, anchor="sometimes")
    assert routines.raise_due(conn)["raised"] == 1
    assert routines.raise_due(conn)["raised"] == 0  # once per due date
    e = conn.execute("select * from exception_record where type = 'routine_due'").fetchone()
    assert e["need"] == "obligation" and e["key"] == f"{r['code']}:2026-12-04"
    assert conn.execute("select role from work_item where id = %s", (e["item_id"],)).fetchone()["role"] == "operator"
    bar = conn.execute("select * from plan_item where exception_id = %s", (e["id"],)).fetchone()
    assert bar["item_id"] == e["item_id"] and bar["start_at"].date() == date(2026, 12, 4) and bar["source"] == "routine"
    [listed] = [x for x in routines.listing(conn) if x["id"] == r["id"]]
    assert listed["open_occurrence"]["item_id"] == e["item_id"] and not listed["overdue"]
    work.close_case(conn, e["item_id"], "done", "fred", reason="sampled")
    from fuzer.ops import exceptions
    exceptions.reconcile(conn)
    out = routines.raise_due(conn)
    assert out["rolled"] == 1
    assert conn.execute("select next_due from work_routine where id = %s", (r["id"],)).fetchone()["next_due"] == \
        date(2026, 12, 11)  # fixed: a week after the last due date
    conn.commit()


def test_an_sap_plans_next_due_bar_becomes_its_occurrences_job_and_clears_when_sap_rolls_it(conn, monkeypatch):
    today = date.today()
    monkeypatch.setattr(routines, "_today", lambda: today)
    due = (today + timedelta(days=5)).isoformat()
    for k, v in {"task": "Lube P-101", "next_due": due, "interval_days": 90, "kind": "MAINT"}.items():
        conn.execute("insert into golden (fuzer_id, field, value, source, rule) values ('fz-mp-1', %s, %s, 'sap', 't') "
                     "on conflict do nothing", (k, Jsonb(v)))
    plan.sync_maintenance_plans(conn)
    assert routines.sync_sap(conn) == 1
    out = routines.raise_due(conn)
    assert out["raised"] == 1
    bar = conn.execute("select * from plan_item where source = 'sap_plan'").fetchone()
    e = conn.execute("select * from exception_record where id = %s", (bar["exception_id"],)).fetchone()
    assert e["type"] == "routine_due" and bar["item_id"] == e["item_id"]
    assert conn.execute("select count(*) as n from plan_item where exception_id = %s", (e["id"],)).fetchone()["n"] == 1
    conn.execute("update plan_item set status = 'done' where id = %s", (bar["id"],))  # SAP rolled the plan past it
    assert routines.raise_due(conn)["cleared"] == 1
    assert conn.execute("select state from exception_record where id = %s", (e["id"],)).fetchone()["state"] == "cleared"
    conn.commit()
