"""The exception contract (core/exceptions.py) and the event outbox (core/events.py) on an empty database."""

import os
import re
import tempfile

import pytest

from fuzer.core import db, events
from fuzer.core.exceptions import TYPES, ExceptionError, ExceptionType, clear_exception, raise_exception
from fuzer.ops import board
from fuzer.ops import exceptions as impl
from fuzer.resources import PACKAGE


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        c.execute("insert into entity (fuzer_id, kind) values ('fz-pump-1', 'equipment'), ('fz-well-a12', 'well')")
        c.execute("insert into entity_key (kind, key, fuzer_id) values ('tag', 'P-101', 'fz-pump-1')")
        c.execute("insert into crosswalk (source, record_type, source_key, fuzer_id, method, confidence) "
                  "values ('sap', 'equipment', '10004711', 'fz-pump-1', 'key', 1)")
        c.commit()
        yield c


@pytest.fixture
def types(monkeypatch):
    def declare(name, **kw):
        monkeypatch.setitem(TYPES, name, ExceptionType(name, name, **kw))

    return declare


def test_refs_match_entities_in_any_terms_and_unmatched_ones_stay(conn):
    ids, unmatched = impl.match(conn, ["tag:P-101", "sap/10004711", "fz-well-a12", "tag:NOPE", "tag:P-101"])
    assert ids == ["fz-pump-1", "fz-well-a12"] and unmatched == ["tag:NOPE"]


def test_one_open_exception_per_condition_routed_to_its_queue(conn, types):
    types("pump_vibration", role="role:reliability")
    first = raise_exception(conn, "pump_vibration", "P-101", "P-101 vibration high", "sre", refs=["tag:P-101"],
                            priority=0.7, evidence={"mm_s": 9.1})
    again = raise_exception(conn, "pump_vibration", "P-101", "P-101 vibration still high", "sre",
                            refs=["tag:SPARE-9"], evidence={"mm_s": 9.8})
    assert first["raised"] and not again["raised"] and again["id"] == first["id"]
    assert again["raised_count"] == 2 and again["evidence"]["mm_s"] == 9.8
    assert again["subjects"] == ["fz-pump-1"] and again["unmatched"] == ["tag:SPARE-9"]
    case = conn.execute("select * from work_item where id = %s", (first["item_id"],)).fetchone()
    assert case["role"] == "role:reliability" and case["subjects"] == ["fz-pump-1"]
    assert case["payload"]["exception_id"] == first["id"] and case["payload"]["mm_s"] == 9.1
    raised = [e for e in events.pending(conn, "exception.raised") if e["ref"] == f"exception:{first['id']}"]
    assert len(raised) == 1 and raised[0]["payload"]["role"] == "role:reliability"

    cleared = clear_exception(conn, "pump_vibration", "P-101", "sre", reason="back to normal")
    assert cleared["state"] == "cleared"
    assert conn.execute("select state from work_item where id = %s", (first["item_id"],)).fetchone()["state"] == "done"
    assert clear_exception(conn, "pump_vibration", "P-101", "sre") is None
    conn.commit()


def test_a_case_closed_while_the_condition_holds_does_not_hide_it(conn, types):
    from fuzer.core import work

    types("late_material", role="role:planner")
    first = raise_exception(conn, "late_material", "PO-77", "PO 77 late", "sap")
    work.close_case(conn, first["item_id"], "done", "pam")
    second = raise_exception(conn, "late_material", "PO-77", "PO 77 still late", "sap")
    assert second["raised"] and second["id"] != first["id"] and second["item_id"] != first["item_id"]
    assert conn.execute("select state from exception_record where id = %s", (first["id"],)).fetchone()["state"] == "closed"
    conn.commit()


def test_an_exception_nobody_is_routed_to_waits_on_the_board(conn, types):
    types("field_report", audience=("ioc", "field"))
    e = raise_exception(conn, "field_report", "walkdown-3", "Leak at well A-12 flowline", "olga",
                        refs=["fz-well-a12"], value_usd=1200)
    assert e["item_id"] is None
    card = next(c for c in board._exception_cards(conn) if c["key"] == f"exception:{e['id']}")
    assert card["unit_id"] == "fz-well-a12" and card["value_usd"] == 1200
    assert any(r["id"] == e["id"] for r in impl.listing(conn, audience="field"))
    conn.commit()


def test_unknown_types_and_missing_keys_are_refused(conn, types):
    with pytest.raises(ExceptionError, match="no exception type"):
        raise_exception(conn, "nope", "k", "t", "me")
    types("keyed")
    with pytest.raises(ExceptionError, match="needs a key"):
        raise_exception(conn, "keyed", "", "t", "me")
    conn.rollback()


def test_the_outbox_delivers_and_retries_failed_handlers(conn, monkeypatch):
    seen = []
    monkeypatch.setattr(events, "HANDLERS", {})
    events.subscriber("t.ok")(lambda c, e: seen.append(e["payload"]["n"]))
    events.subscriber("t.bad")(lambda c, e: 1 / 0)
    conn.execute("update event_outbox set delivered_at = now()")  # only this test's events
    events.publish(conn, "t.ok", None, {"n": 1})
    bad = events.publish(conn, "t.bad", None, {})
    events.publish(conn, "t.nobody", None, {})
    assert events.deliver(conn) == {"delivered": 2, "failed": 1} and seen == [1]
    row = conn.execute("select * from event_outbox where id = %s", (bad,)).fetchone()
    assert row["delivered_at"] is None and row["attempts"] == 1 and "ZeroDivisionError" not in row["last_error"]
    assert "division by zero" in row["last_error"]


def test_every_subscriber_module_is_loaded_by_deliver():
    declared = set()
    for f in PACKAGE.rglob("*.py"):
        if re.search(r"^@events\.subscriber\(", f.read_text(), re.M):
            declared.add("fuzer." + ".".join(f.relative_to(PACKAGE).with_suffix("").parts))
    assert declared == set(events.SUBSCRIBERS)


def test_work_opened_before_the_contract_is_adopted(conn):
    from fuzer.core import work

    item = work.open_case(conn, "source_issue", "Source x is stale", "fuzer", role="role:data_steward",
                          payload={"dataset": "x", "problem": "stale"})
    iow = work.open_case(conn, "integrity", "IOW 9 breach", "agent:sre", payload={"start": {"iow": "IOW-9"}})
    conn.execute((PACKAGE / "ops" / "schema.sql").read_text())  # idempotent, as on every schema change
    row = conn.execute("select * from exception_record where item_id = %s", (item["id"],)).fetchone()
    assert row["type"] == "source_issue" and row["key"] == "x:stale" and row["state"] == "open"
    row = conn.execute("select * from exception_record where item_id = %s", (iow["id"],)).fetchone()
    assert row["type"] == "iow_breach" and row["key"] == "IOW-9"
    conn.rollback()


def _process(key: str) -> str:
    from fuzer.workflow import bpmn

    body = ('<bpmn:startEvent id="s"/><bpmn:userTask id="u" name="Look" fuzer:role="engineer" fuzer:sla="P1D"/>'
            '<bpmn:endEvent id="e"/><bpmn:sequenceFlow id="f1" sourceRef="s" targetRef="u"/>'
            '<bpmn:sequenceFlow id="f2" sourceRef="u" targetRef="e"/>')
    return (f'<bpmn:definitions xmlns:bpmn="{bpmn.BPMN}" xmlns:fuzer="{bpmn.FUZER}" id="d">'
            f'<bpmn:process id="{key}" name="{key}" isExecutable="true" fuzer:correlationKey="key">{body}'
            '</bpmn:process></bpmn:definitions>')


def test_a_process_type_starts_once_and_gets_repeats_as_evidence(conn, types):
    from fuzer.workflow import engine

    engine.set_person(conn, "olga", ["engineer", "workflow_admin"])
    engine.publish(conn, engine.import_bpmn(conn, _process("exc_proc"), "olga")[0]["id"], "olga")
    types("breach", process="exc_proc")
    first = raise_exception(conn, "breach", "IOW-7", "IOW 7 breached", "agent:sre", evidence={"hours": 3})
    again = raise_exception(conn, "breach", "IOW-7", "IOW 7 breached again", "agent:sre", evidence={"hours": 5})
    assert first["raised"] and not again["raised"] and again["item_id"] == first["item_id"]
    assert conn.execute("select count(*) as n from work_item where type = 'exc_proc'").fetchone()["n"] == 1
    messages = conn.execute("select count(*) as n from work_event where item_id = %s and kind = 'message'",
                            (first["item_id"],)).fetchone()["n"]
    assert messages == 1  # the repeat went to the open process as evidence
    conn.commit()


def test_finished_cases_close_their_exceptions(conn, types):
    from fuzer.core import work
    from fuzer.core.exceptions import reconcile

    types("overdue_pm", role="role:maintenance")
    e = raise_exception(conn, "overdue_pm", "PM-1", "PM 1 overdue", "sap")
    work.close_case(conn, e["item_id"], "done", "mia")
    assert reconcile(conn)["closed"] >= 1
    assert conn.execute("select state from exception_record where id = %s", (e["id"],)).fetchone()["state"] == "closed"
    assert any(x["ref"] == f"exception:{e['id']}" for x in events.pending(conn, "exception.closed"))
    conn.commit()


def test_the_ioc_dispatches_and_the_field_reports_back(conn, types):
    from fuzer.ops import exceptions as ex

    conn.execute("insert into geo_index (fuzer_id, kind, geom_type, minx, miny, maxx, maxy, cx, cy, geometry) values "
                 "('fz-well-a12', 'well', 'Point', 4.1, 52.1, 4.1, 52.1, 4.1, 52.1, '{}') on conflict do nothing")
    conn.execute("insert into edge (src, dst, rel, sources, confidence) values ('fz-pump-1', 'fz-well-a12', 'part_of', '{test}', 1)")
    types("leak", audience=("ioc", "field"))
    e = raise_exception(conn, "leak", "walk-9", "Leak at P-101", "olga", refs=["tag:P-101"],
                        evidence={"seen_by": "walkdown", "drips_per_min": 12.0, "detail": {"x": 1}})
    assert e["item_id"] is None
    sent = ex.dispatch(conn, e["id"], "ioc-ann", "fred", reason="nearest operator")
    assert sent["item_id"] and conn.execute("select assignee from work_item where id = %s",
                                            (sent["item_id"],)).fetchone()["assignee"] == "fred"
    [job] = [j for j in ex.field_jobs(conn, "fred") if j["id"] == e["id"]]
    assert job["assets"][0]["fuzer_id"] == "fz-pump-1" and job["assets"][0]["lat"] == 52.1  # located via its well
    assert ("drips_per_min", "12") in job["seen"] and not any(k == "detail" for k, _ in job["seen"])
    ex.report(conn, e["id"], "fred", "on_site")
    with pytest.raises(ExceptionError, match="say why"):
        ex.report(conn, e["id"], "fred", "could_not_do")
    done = ex.report(conn, e["id"], "fred", "done", note="tightened the gland")
    assert [f["status"] for f in done["evidence"]["field"]] == ["on_site", "done"]
    assert conn.execute("select state from work_item where id = %s", (sent["item_id"],)).fetchone()["state"] == "done"
    assert not [j for j in ex.field_jobs(conn, "fred") if j["id"] == e["id"]]  # done: off their list
    topics = {x["topic"] for x in events.pending(conn) if x["ref"] == f"exception:{e['id']}"}
    assert {"exception.raised", "exception.dispatched", "exception.reported"} <= topics
    conn.commit()


def test_another_system_raises_and_clears_a_type_defined_as_data(conn, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from fuzer.api import ops as ops_api

    app = FastAPI()
    app.include_router(ops_api.router)
    app.dependency_overrides[ops_api.get_conn] = lambda: conn
    client = TestClient(app)

    assert client.put("/exceptions/types/upset", json={"by": "ann", "label": "x"}).status_code == 400  # code's
    r = client.put("/exceptions/types/sap_notification", json={"by": "ann", "label": "SAP notification",
                                                               "role": "role:maintenance", "audience": ["ioc", "field"]})
    assert r.status_code == 200, r.text
    kinds = {k["name"]: k for k in client.get("/exceptions/types").json()}
    assert kinds["sap_notification"]["defined_in"] == "data" and kinds["upset"]["defined_in"] == "code"

    body = {"by": "sap", "type": "sap_notification", "key": "N-1001", "title": "M2 notification: seal leak P-101",
            "refs": ["sap/10004711"], "evidence": {"priority_code": "2"}, "source": "sap"}
    first = client.post("/exceptions", json=body).json()
    assert first["raised"] and first["subjects"] == ["fz-pump-1"] and first["item_id"]
    assert client.post("/exceptions", json=body).json()["raised"] is False
    assert any(x["id"] == first["id"] for x in client.get("/exceptions", params={"audience": "field"}).json())
    assert client.delete("/exceptions/types/sap_notification").status_code == 400  # one is open
    assert client.post("/exceptions", json={**body, "type": "nope"}).status_code == 400

    cleared = client.post("/exceptions/clear", json={"by": "sap", "type": "sap_notification", "key": "N-1001",
                                                     "reason": "notification completed"}).json()
    assert cleared["state"] == "cleared"
    assert conn.execute("select state from work_item where id = %s", (first["item_id"],)).fetchone()["state"] == "done"
    assert client.delete("/exceptions/types/sap_notification").json() == {"removed": "sap_notification"}


def test_a_dispatch_is_posted_to_the_alert_webhook(conn, types, monkeypatch):
    from fuzer.ops import exceptions as ex
    from fuzer.sources import alerts

    posted = []
    monkeypatch.setattr(alerts, "post", lambda text: posted.append(text) or True)
    types("gas_smell", audience=("ioc", "field"))
    e = raise_exception(conn, "gas_smell", "pad-4", "Gas smell at pad 4", "olga")
    ex.dispatch(conn, e["id"], "ann", "fred", reason="closest")
    conn.commit()
    events.deliver(conn)
    assert "Dispatched to fred: Gas smell at pad 4 (closest)" in posted
