"""The approved write-back queue (P5.7, #634): nothing reaches a source system without a named approver, it goes by
the system's cadence, a failure goes back to the approver and a confirmed write releases the case."""

import json
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from fuzer.core import db, gates
from fuzer.ops import writeback as wb
from fuzer.resources import PACKAGE
from fuzer.sources import catalog
from fuzer.workflow import engine

T0 = datetime(2026, 11, 12, 6, 3, tzinfo=UTC)


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        engine._parsed.clear()
        engine.load_defaults(c)
        for name, roles in (("pat", ["maintenance_planner"]), ("sam", ["data_steward"]), ("ines", ["ops_lead"])):
            engine.set_person(c, name, roles)
        catalog.add_system(c, "sap", "test", files=False)
        catalog.add_system(c, "pi", "test", files=False, writeback={"accepts": ["annotation"], "limit": 1})
        c.commit()
        yield c


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("FUZER_DATA_DIR", str(tmp_path))
    return tmp_path


def queued(conn, kind="notification", record="10004002", system="sap", **payload) -> dict:
    row = wb.propose(conn, system, kind, payload or {"text": "Gland repacked"}, "agent:maintenance", record=record,
                     action="complete")
    conn.commit()
    return row


def test_a_proposal_waits_in_the_approvers_queue(conn):
    row = queued(conn, record="1")
    assert row["state"] == "awaiting_approval" and row["approver_role"] == "maintenance_planner"
    case = conn.execute("select role, type, state from work_item where id = %s", (row["item_id"],)).fetchone()
    assert (case["role"], case["type"], case["state"]) == ("maintenance_planner", "writeback", "open")
    assert [h["state"] for h in wb.history(conn, row["id"])] == ["drafted", "awaiting_approval"]
    assert queued(conn, record="1")["id"] == row["id"]  # the same proposal again: idempotent
    with pytest.raises(wb.WritebackError, match="pi doesn't take notification"):
        wb.propose(conn, "pi", "notification", {}, "pat")


def test_only_a_named_approver_in_the_right_role_approves(conn):
    row = queued(conn, record="2")
    with pytest.raises(gates.GateRefused):
        wb.approve(conn, row["id"], "agent:maintenance")
    with pytest.raises(gates.GateRefused):
        wb.approve(conn, row["id"], "sap webhook")
    with pytest.raises(wb.WritebackError, match="needs one of maintenance_planner"):
        wb.approve(conn, row["id"], "sam")
    conn.rollback()
    assert wb.get(conn, row["id"])["state"] == "awaiting_approval"


def test_approved_goes_in_the_next_batch_through_the_export_file_and_a_confirm_releases_the_case(conn, data):
    row = queued(conn, record="3")
    ok = wb.approve(conn, row["id"], "pat", {"text": "Gland repacked; no leak"}, now=T0)
    conn.commit()
    assert ok["due_at"] == datetime(2026, 11, 12, 6, 15, tzinfo=UTC)  # the 15-minute maintenance batch
    assert wb.run(conn, T0 + timedelta(minutes=5))["sent"] == 0
    assert wb.run(conn, T0 + timedelta(minutes=13))["sent"] >= 1
    sent = wb.get(conn, row["id"])
    assert sent["state"] == "sent"
    [line] = [json.loads(x) for x in (data / "writeback" / "sap.jsonl").read_text().splitlines()
              if json.loads(x)["id"] == row["id"]]
    assert line["approved_by"] == "pat" and line["payload"]["text"] == "Gland repacked; no leak"
    wb.confirm(conn, row["id"], "pat", "applied in SAP")
    conn.commit()
    assert conn.execute("select state from work_item where id = %s", (row["item_id"],)).fetchone()["state"] == "done"
    assert [h["state"] for h in wb.history(conn, row["id"])][-3:] == ["approved", "sent", "confirmed"]


@pytest.mark.parametrize("name", sorted(wb.ADAPTERS))
def test_every_adapter_refuses_a_row_without_a_recorded_approver(conn, name):
    row = queued(conn, record=f"adapter-{name}")
    with pytest.raises(wb.WritebackError, match="no recorded approver"):
        wb.ADAPTERS[name](conn, row)
    forged = {**row, "state": "approved", "approved_by": "agent:maintenance"}
    with pytest.raises(wb.WritebackError, match="no recorded approver"):
        wb.ADAPTERS[name](conn, forged)
    with pytest.raises(wb.WritebackError):
        wb.send(conn, row["id"])
    conn.rollback()


def test_a_resend_writes_nothing_twice(conn, data):
    row = queued(conn, record="4")
    approved = wb.approve(conn, row["id"], "pat", now=T0)
    wb._export(conn, approved)
    wb._export(conn, approved)
    lines = [x for x in (data / "writeback" / "sap.jsonl").read_text().splitlines() if json.loads(x)["id"] == row["id"]]
    assert len(lines) == 1
    conn.rollback()


def test_a_failure_goes_back_to_the_approver_and_is_never_retried_on_its_own(conn, monkeypatch):
    row = queued(conn, record="5")
    wb.approve(conn, row["id"], "pat", now=T0)
    conn.commit()

    def refuse(conn, row):
        wb._check_approved(row)
        raise RuntimeError("SAP: notification 5 is locked by another user")

    monkeypatch.setitem(wb.ADAPTERS, "export", refuse)
    wb.run(conn, T0 + timedelta(hours=1))
    failed = wb.get(conn, row["id"])
    assert failed["state"] == "failed" and "locked by another user" in failed["error"]
    said = conn.execute("select data->>'text' as t from work_event where item_id = %s and kind = 'commented' "
                        "order by id desc limit 1", (failed["item_id"],)).fetchone()["t"]
    assert "locked by another user" in said and "Approve it again" in said
    assert wb.run(conn, T0 + timedelta(hours=2))["sent"] == 0 and wb.get(conn, row["id"])["state"] == "failed"
    monkeypatch.undo()
    wb.approve(conn, row["id"], "pat", now=T0 + timedelta(hours=2))
    conn.commit()
    wb.run(conn, T0 + timedelta(hours=3))
    assert wb.get(conn, row["id"])["state"] == "sent"


def test_cadence_and_the_limit_per_run(conn):
    assert wb.next_send("now", T0) == T0
    assert wb.next_send("daily 02:00", T0) == datetime(2026, 11, 13, 2, 0, tzinfo=UTC)
    assert wb.next_send("window 22:00-04:00", T0) == datetime(2026, 11, 12, 22, 0, tzinfo=UTC)
    assert wb.next_send("window 05:00-07:00", T0) == T0
    with pytest.raises(wb.WritebackError):
        wb.parse_cadence("whenever")
    assert wb.policy(conn, "sap")["kinds"]["safety_notification"]["cadence"] == "now"
    assert wb.policy(conn, "sap")["kinds"]["master_data"]["approvers"] == ["data_steward"]
    a, b = (wb.propose(conn, "pi", "annotation", {"text": t}, "ines", record="K-201") for t in ("trip", "restart"))
    for r in (a, b):
        wb.approve(conn, r["id"], "ines", now=T0)
    conn.commit()
    out = wb.run(conn, T0 + timedelta(minutes=30))
    assert out["sent"] == 1 and out["held"] == 1  # pi takes one write per run


def test_rejecting_cancels_the_case(conn):
    row = queued(conn, record="6")
    wb.reject(conn, row["id"], "pat", "SAP already updated by hand")
    conn.commit()
    assert conn.execute("select state from work_item where id = %s", (row["item_id"],)).fetchone()["state"] == "cancelled"
    with pytest.raises(wb.WritebackError):
        wb.approve(conn, row["id"], "pat")
    conn.rollback()


def test_no_code_writes_to_a_source_system_outside_the_queue():
    """The adapters (and the demo's export-file writer they use) are called from the queue only."""
    callers = {}
    for f in PACKAGE.rglob("*.py"):
        text = f.read_text()
        for name in ("apply_update(", "_export(", "_demo_csv(", "ADAPTERS["):
            uses = re.findall(rf"(?<![\w]){re.escape(name)}", text)
            defs = re.findall(rf"def {re.escape(name)}", text)
            if len(uses) > len(defs):
                callers.setdefault(name, set()).add(f.relative_to(PACKAGE).as_posix())
    assert callers == {"apply_update(": {"ops/writeback.py"}, "_export(": {"ops/writeback.py"},
                       "ADAPTERS[": {"ops/writeback.py"}}, callers
