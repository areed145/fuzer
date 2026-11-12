"""The exception contract (core/exceptions.py) and the event outbox (core/events.py) on an empty database."""

import os
import re
import tempfile

import pytest

from fuzer.core import db, events
from fuzer.core.exceptions import (
    TYPES,
    ExceptionError,
    ExceptionType,
    clear_exception,
    raise_exception,
)
from fuzer.core.findings import external, text
from fuzer.ops import board
from fuzer.ops import exceptions as impl
from fuzer.resources import PACKAGE


def pairs(row: dict) -> list[tuple[str, str]]:
    """A row's finding as a person reads it: each item's label and its value as text."""
    return [(i["label"], text(i)) for i in row["finding"]["items"]]


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        from fuzer.workflow import engine

        engine.load_defaults(c)  # the standard processes: queue cases run as queue_item (#509)
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
                            priority=0.7, finding=external("test", {"mm_s": 9.1}))
    again = raise_exception(conn, "pump_vibration", "P-101", "P-101 vibration still high", "sre",
                            refs=["tag:SPARE-9"], finding=external("test", {"mm_s": 9.8}))
    assert first["raised"] and not again["raised"] and again["id"] == first["id"]
    assert again["raised_count"] == 2 and again["evidence"]["mm_s"] == 9.8
    assert again["subjects"] == ["fz-pump-1"] and again["unmatched"] == ["tag:SPARE-9"]
    case = conn.execute("select * from work_item where id = %s", (first["item_id"],)).fetchone()
    assert case["role"] == "reliability" and case["subjects"] == ["fz-pump-1"]  # the engine's bare role: its queue
    assert case["payload"]["exception_id"] == first["id"] and case["payload"]["mm_s"] == 9.1
    raised = [e for e in events.pending(conn, "exception.raised") if e["ref"] == f"exception:{first['id']}"]
    assert len(raised) == 1 and raised[0]["payload"]["role"] == "reliability"

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
    # Load the real subscribers first: deliver() imports them, and a first import while HANDLERS is swapped out
    # would register them into the throwaway dict, leaving the real one without them for the rest of the run
    # (that is what made the close-out test flake: its event was delivered to nobody).
    events.load_subscribers()
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
        if re.search(r"^@events\.subscriber\(", f.read_text(), re.MULTILINE):
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
    first = raise_exception(conn, "breach", "IOW-7", "IOW 7 breached", "agent:sre", finding=external("test", {"hours": 3}))
    again = raise_exception(conn, "breach", "IOW-7", "IOW 7 breached again", "agent:sre", finding=external("test", {"hours": 5}))
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
                        finding=external("test", {"seen_by": "walkdown", "drips_per_min": 12.0, "detail": {"x": 1}}))
    assert e["item_id"] is None
    sent = ex.dispatch(conn, e["id"], "ioc-ann", "fred", reason="nearest operator")
    assert sent["item_id"] and conn.execute("select assignee from work_item where id = %s",
                                            (sent["item_id"],)).fetchone()["assignee"] == "fred"
    [job] = [j for j in ex.field_jobs(conn, "fred") if j["id"] == e["id"]]
    assert job["assets"][0]["fuzer_id"] == "fz-pump-1" and job["assets"][0]["lat"] == 52.1  # located via its well
    said = {i["label"]: i for i in job["finding"]["items"]}
    assert said["Drips per min"] == {"label": "Drips per min", "value": 12.0, "kind": "number"} and "Detail" not in said
    assert job["finding"]["origin"] == "external" and job["finding"]["subjects"] == ["fz-pump-1"]
    assert ("Drips per min", "12") in pairs(job)
    assert job["sent"]["by"] == "ioc-ann" and job["sent"]["reason"] == "nearest operator"  # what the IOC asked for
    ex.report(conn, e["id"], "fred", "on_site")
    with pytest.raises(ExceptionError, match="say why"):
        ex.report(conn, e["id"], "fred", "could_not_do")
    done = ex.report(conn, e["id"], "fred", "done", note="tightened the gland")
    assert [f["status"] for f in done["evidence"]["field"]] == ["on_site", "done"]
    assert done["state"] == "closed"  # with its case, so the IOC doesn't see finished work as open
    assert conn.execute("select state from work_item where id = %s", (sent["item_id"],)).fetchone()["state"] == "done"
    assert not [j for j in ex.field_jobs(conn, "fred") if j["id"] == e["id"]]  # done: off their list
    topics = {x["topic"] for x in events.pending(conn) if x["ref"] == f"exception:{e['id']}"}
    assert {"exception.raised", "exception.dispatched", "exception.reported"} <= topics
    conn.commit()


PIXEL = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
         "AAAABJRU5ErkJggg==")


def test_a_report_carries_a_reading_and_a_photo_the_ioc_sees(conn, types):
    """P5.3b (#642): Done with a reading and a photo; the case's history says it in words and links the photo,
    which the API serves back as the image the phone sent."""
    from fuzer.api import ops as api
    from fuzer.ops import exceptions as ex

    types("leak", audience=("ioc", "field"))
    e = raise_exception(conn, "leak", "walk-photo", "Leak at P-102 (photo)", "olga", refs=["tag:P-102"])
    ex.dispatch(conn, e["id"], "ioc-ann", "fred", reason="look at the gland")
    conn.commit()
    with pytest.raises(ExceptionError, match="needs its unit"):
        ex.report(conn, e["id"], "fred", "note", "hot", reading={"value": 72})
    with pytest.raises(ExceptionError, match="JPEG, PNG or WebP"):
        ex.report(conn, e["id"], "fred", "note", "x", photos=["data:text/html;base64,PGI+"])
    conn.rollback()
    row = api.exception_report(e["id"], api.FieldReport(by="fred", status="done", note="gland repacked",
                                                        reading={"value": 72.5, "unit": "°C", "what": "Gland"},
                                                        photos=[PIXEL]), conn)
    [entry] = row["evidence"]["field"]
    assert entry["reading"] == {"value": 72.5, "unit": "°C", "what": "Gland"} and len(entry["photos"]) == 1
    said = conn.execute("select data from work_event where item_id = %s and kind = 'commented' order by id desc limit 1",
                        (row["item_id"],)).fetchone()["data"]
    assert said["text"] == "Field: Done: gland repacked (Gland 72.5 °C; 1 photo)"
    assert said["attachments"] == [{"kind": "photo", "exception_id": e["id"], "photo_id": entry["photos"][0]}]
    img = api.exception_photo(e["id"], entry["photos"][0], conn)
    assert img.media_type == "image/png" and img.body.startswith(b"\x89PNG")
    with pytest.raises(Exception, match="404"):
        api.exception_photo(e["id"] + 999, entry["photos"][0], conn)


def test_a_report_made_offline_arrives_once_with_its_time(conn, types):
    """P5.3c (#643): the phone sends a report it kept without signal, then again after losing the answer: it is
    applied once and keeps the time it was made, which the case history says."""
    from datetime import datetime, timedelta

    from fuzer.api import ops as api
    from fuzer.ops import exceptions as ex

    types("leak", audience=("ioc", "field"))
    e = raise_exception(conn, "leak", "walk-offline", "Leak at P-103 (offline)", "olga", refs=["tag:P-103"])
    ex.dispatch(conn, e["id"], "ioc-ann", "fred", reason="look at it")
    conn.commit()
    made = (datetime.now().astimezone() - timedelta(minutes=40)).replace(microsecond=0)
    sent = api.FieldReport(by="fred", status="note", note="drip rate 12/min", client_id="phone-1-abc", at=made.isoformat())
    api.exception_report(e["id"], sent, conn)
    again = api.exception_report(e["id"], sent, conn)
    entries = [f for f in again["evidence"]["field"] if f.get("client_id") == "phone-1-abc"]
    assert len(entries) == 1 and datetime.fromisoformat(entries[0]["at"]) == made and entries[0]["received_at"]
    said = conn.execute("select count(*) as n, max(data->>'text') as t from work_event where item_id = %s and "
                        "kind = 'commented'", (again["item_id"],)).fetchone()
    assert said["n"] == 1 and "sent when back in signal" in said["t"]
    future = ex.report(conn, e["id"], "fred", "note", "clock ahead", client_id="phone-1-def",
                       at=(datetime.now().astimezone() + timedelta(days=1)).isoformat())
    assert datetime.fromisoformat(future["evidence"]["field"][-1]["at"]) <= datetime.now().astimezone()
    conn.commit()


def test_the_field_reporting_back_returns_the_ioc_step_to_its_queue(conn, types):
    """Dispatching a process case hands its open person step (the IOC's decision) to the operator; when they say
    done, or that they couldn't, it goes back to whoever had it, so it reappears in the IOC's Inbox."""
    from fuzer.ops import exceptions as ex
    from fuzer.workflow import engine

    engine.set_person(conn, "olga", ["engineer", "workflow_admin"])
    engine.publish(conn, engine.import_bpmn(conn, _process("field_proc"), "olga")[0]["id"], "olga")
    types("pressure_low", process="field_proc", audience=("ioc", "field"))
    e = raise_exception(conn, "pressure_low", "WHP-3", "Low wellhead pressure", "agent:sre", refs=["fz-well-a12"],
                        finding=external("test", {"start": "2025-12-30T00:00", "ongoing": True, "upset_id": 3}))
    step = lambda: conn.execute("select assignee, role from work_token where item_id = %s and kind = 'user' "
                                "and state = 'waiting'", (e["item_id"],)).fetchone()
    assert step() == {"assignee": None, "role": "engineer"}
    ex.dispatch(conn, e["id"], "olga", "fred", reason="check the gauge at the wellhead")
    assert step()["assignee"] == "fred"
    [job] = [j for j in ex.field_jobs(conn, "fred") if j["id"] == e["id"]]
    assert job["sent"]["reason"] == "check the gauge at the wellhead" and job["task_name"] == "Look"
    assert ("Start", "2025-12-30 00:00") in pairs(job) and ("Ongoing", "yes") in pairs(job)
    assert {"label": "Start", "value": "2025-12-30T00:00", "kind": "time"} in job["finding"]["items"]
    assert not any(k == "Upset id" for k, _ in pairs(job))  # internal ids aren't something to look for on site
    ex.report(conn, e["id"], "fred", "on_site")
    assert step()["assignee"] == "fred"  # on site: still theirs
    ex.report(conn, e["id"], "fred", "done", note="gauge reads 13 barg; wing valve was part closed, reopened")
    assert step() == {"assignee": None, "role": "engineer"}  # back in the engineer queue
    assert not [j for j in ex.field_jobs(conn, "fred") if j["id"] == e["id"]]
    back = conn.execute("select data from work_event where item_id = %s and kind = 'handed_off' order by id desc "
                        "limit 1", (e["item_id"],)).fetchone()["data"]
    assert back["to"] == "role:engineer" and back["reason"].startswith("Back from the field: done")
    assert conn.execute("select 1 from work_event where item_id = %s and kind = 'commented' and data->>'text' like "
                        "'%%wing valve%%'", (e["item_id"],)).fetchone()  # what they found is on the case history
    conn.commit()


def test_a_job_the_field_could_not_do_goes_back_to_the_dispatcher(conn, types):
    from fuzer.ops import exceptions as ex

    types("walkdown", audience=("ioc", "field"))
    e = raise_exception(conn, "walkdown", "walk-12", "Smell of gas near P-101", "olga", refs=["tag:P-101"])
    sent = ex.dispatch(conn, e["id"], "ioc-ann", "fred", reason="walk the area")
    ex.report(conn, e["id"], "fred", "could_not_do", note="area barriered off for lifting")
    case = conn.execute("select state, assignee from work_item where id = %s", (sent["item_id"],)).fetchone()
    assert case == {"state": "open", "assignee": "ioc-ann"}
    assert not [j for j in ex.field_jobs(conn, "fred") if j["id"] == e["id"]]
    conn.commit()


def test_a_job_routed_to_the_operators_queue_reaches_the_field(conn, types):
    """A type routed to role:operator waits in the operators' Inbox queue and on their My jobs until one takes it."""
    from fuzer.core import work
    from fuzer.ops import exceptions as ex
    from fuzer.workflow import engine

    engine.set_person(conn, "otto", ["operator"])
    types("scada_trip", role="role:operator", audience=("ioc", "field"))
    e = raise_exception(conn, "scada_trip", "PSHH-1", "Compressor tripped on high discharge pressure", "scada",
                        refs=["tag:P-101"])
    assert [q for q in work._engine().my_work(conn, "otto")["queue"] if q["item_id"] == e["item_id"]]
    [job] = [j for j in ex.field_jobs(conn, "otto") if j["id"] == e["id"]]
    assert job["queued"] is True
    ex.dispatch(conn, e["id"], "otto", "otto", reason="taking it")
    [job] = [j for j in ex.field_jobs(conn, "otto") if j["id"] == e["id"]]
    assert job["queued"] is False
    conn.commit()


def test_a_job_taken_from_the_queue_keeps_what_the_ioc_asked_and_goes_back_to_it(conn, types):
    """The IOC dispatches to the operators' queue with what to check; an operator takes it. Their job still says what
    was asked and by whom, and Could not do hands it back to the dispatcher, not to the queue it was taken from."""
    from fuzer.ops import exceptions as ex
    from fuzer.workflow import engine

    engine.set_person(conn, "ivy", ["ops_lead"])
    engine.set_person(conn, "olly", ["operator"])
    types("packing_leak", audience=("ioc", "field"))
    e = raise_exception(conn, "packing_leak", "P-101-PK", "Packing leak on P-101", "sap", refs=["tag:P-101"])
    ex.dispatch(conn, e["id"], "ivy", "role:operator", reason="Check the leak rate and tighten the gland if safe")
    ex.dispatch(conn, e["id"], "olly", "olly", reason="taken from the queue")
    [job] = [j for j in ex.field_jobs(conn, "olly") if j["id"] == e["id"]]
    assert job["sent"]["by"] == "ivy" and job["sent"]["reason"].startswith("Check the leak rate")
    ex.report(conn, e["id"], "olly", "could_not_do", "Gland at end of travel; needs repacking")
    item = conn.execute("select assignee, role from work_item where id = %s", (job["item_id"],)).fetchone()
    assert item["assignee"] == "ivy"
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
    r = client.put("/exceptions/types/scada_alarm", json={"by": "ann", "label": "SCADA alarm",
                                                          "role": "role:maintenance", "audience": ["ioc", "field"]})
    assert r.status_code == 400 and "no role queue 'maintenance'" in r.text  # a queue no Inbox would show
    r = client.put("/exceptions/types/scada_alarm", json={"by": "ann", "label": "SCADA alarm",
                                                          "role": "operator", "audience": ["ioc", "field"]})
    assert r.status_code == 200 and r.json()["role"] == "role:operator", r.text
    kinds = {k["name"]: k for k in client.get("/exceptions/types").json()}
    assert kinds["scada_alarm"]["defined_in"] == "data" and kinds["upset"]["defined_in"] == "code"

    body = {"by": "scada", "type": "scada_alarm", "key": "N-1001", "title": "P-101 seal leak alarm",
            "refs": ["sap/10004711"], "evidence": {"alarm_priority": "high"}, "source": "scada"}
    first = client.post("/exceptions", json=body).json()
    assert first["raised"] and first["subjects"] == ["fz-pump-1"] and first["item_id"]
    assert first["unmatched_note"] is None
    odd = client.post("/exceptions", json={**body, "key": "N-1002", "refs": ["tag:KRGP-B1-PT-2001"]}).json()
    assert odd["unmatched"] == ["tag:KRGP-B1-PT-2001"] and "matched nothing" in odd["unmatched_note"]
    assert "tag:P-101" in odd["unmatched_note"]  # the forms Fuzer does know, with an example from this register
    forms = {f["form"]: f for f in client.get("/exceptions/refs").json()}
    assert forms["tag:<key>"]["example"] == "tag:P-101" and forms["sap/<source key>"]["example"] == "sap/10004711"
    client.post("/exceptions/clear", json={"by": "scada", "type": "scada_alarm", "key": "N-1002"})
    assert client.post("/exceptions", json=body).json()["raised"] is False
    assert any(x["id"] == first["id"] for x in client.get("/exceptions", params={"audience": "field"}).json())
    assert client.delete("/exceptions/types/scada_alarm").status_code == 400  # one is open
    assert client.post("/exceptions", json={**body, "type": "nope"}).status_code == 400

    cleared = client.post("/exceptions/clear", json={"by": "scada", "type": "scada_alarm", "key": "N-1001",
                                                       "reason": "alarm returned to normal"}).json()
    assert cleared["state"] == "cleared"
    assert conn.execute("select state from work_item where id = %s", (first["item_id"],)).fetchone()["state"] == "done"
    assert client.delete("/exceptions/types/scada_alarm").json() == {"removed": "scada_alarm"}


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


def test_dispatching_an_upset_sends_it_through_its_own_field_check(conn, types):
    """The upset's Triage declares a way to the field: dispatching it to an operator completes Triage with
    `field_check` and hands the process's Field check step to them, so the engineer's decision never lands in the
    operator's list. Their report completes the Field check and Triage comes back to whoever sent them."""
    from fuzer.ops import exceptions as ex
    from fuzer.workflow import engine

    engine.load_defaults(conn)
    engine.set_person(conn, "erin", ["engineer"])
    engine.set_person(conn, "otto", ["operator"])
    types("upset_t", process="upset", audience=("ioc", "field"))
    e = raise_exception(conn, "upset_t", "UP-1", "Low wellhead pressure across 3 assets", "agent:sre",
                        refs=["fz-well-a12"], finding=external("test", {"incident_id": "UP-1", "start": "2025-12-30T00:00"}))
    steps = lambda: conn.execute("select element, assignee, role from work_token where item_id = %s and kind = 'user' "
                                 "and state = 'waiting' order by id", (e["item_id"],)).fetchall()
    assert [s["element"] for s in steps()] == ["triage"]
    ex.dispatch(conn, e["id"], "erin", "otto", reason="read the wellhead gauge")
    assert steps() == [{"element": "field_check", "assignee": "otto", "role": "operator"}]
    [job] = [j for j in ex.field_jobs(conn, "otto") if j["id"] == e["id"]]
    assert job["task_name"] == "Field check" and job["own_step"] is True
    assert job["sent"]["reason"] == "read the wellhead gauge" and job["sent"]["by"] == "erin"
    ex.report(conn, e["id"], "otto", "done", note="gauge reads 13 barg, wing valve part closed")
    assert steps() == [{"element": "triage", "assignee": "erin", "role": "engineer"}]  # back with whoever sent them
    data = conn.execute("select data from work_process where item_id = %s", (e["item_id"],)).fetchone()["data"]
    assert data["field_result"] == "done" and "13 barg" in data["field_note"]
    [row] = [r for r in engine.my_work(conn, "erin")["mine"] if r["item_id"] == e["item_id"]]
    assert row["handed_reason"].startswith("Back from the field: done: gauge reads 13 barg")  # why it is back
    assert not [j for j in ex.field_jobs(conn, "otto") if j["id"] == e["id"]]
    conn.commit()


def test_an_operator_handed_another_roles_step_is_not_offered_it(conn, types):
    """A step without a field route handed to an operator stays the IOC's decision: My jobs doesn't offer to open it."""
    from fuzer.ops import exceptions as ex
    from fuzer.workflow import engine

    engine.set_person(conn, "olga", ["engineer", "workflow_admin"])
    engine.set_person(conn, "otto", ["operator"])
    engine.publish(conn, engine.import_bpmn(conn, _process("own_proc"), "olga")[0]["id"], "olga")
    types("own_t", process="own_proc", audience=("ioc", "field"))
    e = raise_exception(conn, "own_t", "OWN-1", "Gauge reads odd", "agent:sre", refs=["fz-well-a12"])
    ex.dispatch(conn, e["id"], "olga", "otto", reason="look at it")
    [job] = [j for j in ex.field_jobs(conn, "otto") if j["id"] == e["id"]]
    assert job["task_id"] and job["own_step"] is False
    conn.commit()


def test_equipment_reads_as_the_tag_it_is_installed_at(conn, types):
    """An operator finds "20-K-2100B 1st stage compressor" on site, not "S/N 81710"."""
    from psycopg.types.json import Jsonb

    from fuzer.graph.queries.records import names
    from fuzer.ops import exceptions as ex

    conn.execute("insert into entity (fuzer_id, kind) values ('fz-equipment-81710', 'equipment'), ('fz-tag-k2100b', 'tag')")
    conn.execute("insert into entity_label (fuzer_id, label) values ('fz-equipment-81710', 'S/N 81710'), "
                 "('fz-tag-k2100b', '20-K-2100B')")
    conn.execute("insert into golden (fuzer_id, field, value, source, rule) values "
                 "('fz-equipment-81710', 'description', %s, 'sap', 't')", (Jsonb("1st stage compressor"),))
    conn.execute("insert into edge (src, dst, rel, sources, confidence) values "
                 "('fz-equipment-81710', 'fz-tag-k2100b', 'installed_at', '{sap}', 1)")
    types("vib", audience=("ioc", "field"))
    e = raise_exception(conn, "vib", "V-1", "High vibration", "scada", refs=["fz-equipment-81710"])
    ex.dispatch(conn, e["id"], "ioc-ann", "fred")
    [job] = [j for j in ex.field_jobs(conn, "fred") if j["id"] == e["id"]]
    assert job["assets"][0]["label"] == "20-K-2100B 1st stage compressor"
    assert names(conn, ["fz-equipment-81710"])["fz-equipment-81710"]["at"] == "20-K-2100B"
    conn.commit()


# ---- one event, one case (#464) -------------------------------------------------------------------------------------

@pytest.fixture
def compressor(conn):
    """A compressor tag with its equipment installed at it, a pressure transmitter part of it and that
    transmitter's historian point, as Kestrel's B1-K-2001 is: what SAP, the IOW check and the SRE each refer to."""
    conn.execute("insert into entity (fuzer_id, kind) values ('fz-k-2001', 'tag'), ('fz-pt-2001', 'tag'), "
                 "('fz-eq-82001', 'equipment'), ('fz-pv-2001', 'timeseries'), ('fz-area-b1', 'area'), "
                 "('fz-v-1001', 'tag') on conflict do nothing")
    conn.execute(
        """
        insert into edge (src, rel, dst, sources, confidence) values
          ('fz-eq-82001', 'installed_at', 'fz-k-2001', '{test}', 1), ('fz-pt-2001', 'part_of', 'fz-k-2001', '{test}', 1),
          ('fz-pt-2001', 'has_timeseries', 'fz-pv-2001', '{test}', 1), ('fz-k-2001', 'part_of', 'fz-area-b1', '{test}', 1),
          ('fz-v-1001', 'part_of', 'fz-area-b1', '{test}', 1)
        on conflict do nothing
        """)
    conn.commit()
    return {"tag": "fz-k-2001", "pt": "fz-pt-2001", "equipment": "fz-eq-82001", "point": "fz-pv-2001",
            "elsewhere": "fz-v-1001"}


@pytest.fixture
def three_sources(conn, types, compressor):
    types("sap_note", role="role:maintenance")
    types("iow_hi", role="role:engineer")
    types("upset_x", role="role:engineer")
    yield
    # each test's event is over, so the next test's trip is an event of its own
    conn.rollback()
    conn.execute("update work_item set state = 'done' where id in (select item_id from exception_record "
                 "where subjects && %s::text[] and state = 'open')", (list(compressor.values()),))
    conn.execute("update exception_record set state = 'closed' where subjects && %s::text[] and state = 'open'",
                 (list(compressor.values()),))
    conn.commit()


def _raise_trip(conn, c, n):
    sap = raise_exception(conn, "sap_note", f"N-{n}", "High vibration trip on sales gas compressor", "sap",
                          refs=[c["equipment"]], value_usd=5000, finding=external("test", {"notif_type": "M2"}))
    iow = raise_exception(conn, "iow_hi", f"IOW-{n}", "Inlet pressure IOW breach", "limits", refs=[c["point"]],
                          value_usd=20000, finding=external("test", {"minutes_outside": 95.0}))
    sre = raise_exception(conn, "upset_x", f"U-{n}", "Step change across six assets", "sre", refs=[c["pt"]],
                          finding=external("test", {"detections": 6}))
    return sap, iow, sre


def test_one_event_reported_by_three_systems_is_one_case(conn, compressor, three_sources):
    sap, iow, sre = _raise_trip(conn, compressor, 1)
    assert sap["item_id"] and not sap.get("joined")
    assert iow["joined"] == sre["joined"] == sap["id"] and iow["item_id"] == sre["item_id"] == sap["item_id"]
    assert conn.execute("select count(*) as n from work_item where id = any(%s)",
                        ([sap["item_id"], iow["item_id"], sre["item_id"]],)).fetchone()["n"] == 1
    [row] = [r for r in impl.listing(conn) if sap["id"] in [r["id"]] + [j["id"] for j in r["joined"]]]
    assert row["id"] == sap["id"] and [j["id"] for j in row["joined"]] == [iow["id"], sre["id"]]
    assert row["value_usd"] == 20000  # the event carries its largest value
    assert set(row["subjects"]) == {compressor["equipment"], compressor["point"], compressor["pt"]}
    notes = [r["data"]["text"] for r in conn.execute(
        "select data from work_event where item_id = %s and kind = 'commented' order by id", (sap["item_id"],))]
    assert notes == ["Also reported: iow_hi from limits: Inlet pressure IOW breach",
                     "Also reported: upset_x from sre: Step change across six assets"]
    sources = impl.on_case(conn, sap["item_id"])
    assert [s["id"] for s in sources] == [sap["id"], iow["id"], sre["id"]]
    assert ("Minutes outside", "95") in pairs(sources[1])
    assert sources[1]["finding"]["value_usd"] == 20000 and sources[0]["finding"]["detector"] == "test"
    again = raise_exception(conn, "iow_hi", "IOW-1", "Inlet pressure IOW breach", "limits", refs=[compressor["point"]])
    assert not again["raised"] and again["item_id"] == sap["item_id"]  # a repeat stays on the shared case
    [row] = [r for r in impl.listing(conn) if r["id"] == sap["id"]]
    assert row["reports"] == 4  # seen N× counts every source on the case and its repeats (#496)
    conn.commit()


def test_another_asset_or_a_type_that_does_not_join_gets_its_own_case(conn, compressor, three_sources, types):
    sap, _, _ = _raise_trip(conn, compressor, 2)
    other = raise_exception(conn, "iow_hi", "IOW-V", "Inlet separator level", "limits", refs=[compressor["elsewhere"]])
    assert not other.get("joined") and other["item_id"] != sap["item_id"]  # same area is not the same event
    types("data_gap", role="role:engineer", joins=False)
    gap = raise_exception(conn, "data_gap", "G-1", "Point stale", "sources", refs=[compressor["point"]])
    assert not gap.get("joined") and gap["item_id"] != sap["item_id"]
    conn.commit()


def test_a_case_shows_the_log_entries_about_its_event_around_its_window(conn, compressor, three_sources):
    """P3.5a: an entry mentioning the compressor's transmitter in the event's window is shown; one about another asset
    in the same area, or about the compressor a day later, is not."""
    from fuzer.core.findings import Finding
    from fuzer.ops import around

    entries = [("fz-log-a", "2026-11-11 03:12", "fz-pt-2001", "Tripped on high vibration"),
               ("fz-log-b", "2026-11-11 03:20", "fz-v-1001", "Separator level steady"),
               ("fz-log-c", "2026-11-12 09:00", "fz-k-2001", "Running steady")]
    for fid, at, about, words in entries:
        conn.execute("insert into entity (fuzer_id, kind) values (%s, 'log_entry') on conflict do nothing", (fid,))
        conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, 'timestamp', to_jsonb(%s::text), "
                     "'test', 'test'), (%s, 'text', to_jsonb(%s::text), 'test', 'test') on conflict do nothing",
                     (fid, at, fid, words))
        conn.execute("insert into edge (src, rel, dst, sources, confidence) values (%s, 'mentions', %s, '{test}', 1) "
                     "on conflict do nothing", (fid, about))
    trip = raise_exception(conn, "iow_hi", "IOW-AROUND", "Trip", "limits", refs=[compressor["equipment"]],
                           finding=Finding("test", "deterministic", start="2026-11-11T03:10"))
    ctx = around.context(conn, trip["item_id"])
    assert ctx["window"] == {"start": "2026-11-10T23:10", "end": "2026-11-11T09:10"}
    assert [x["id"] for x in ctx["log"]] == ["fz-log-a"]
    assert impl.anchor_map(conn, ["fz-pt-2001"])["fz-pt-2001"] == impl.anchors(conn, ["fz-pt-2001"])
    conn.commit()


def test_planned_work_and_a_shutdown_suppress_a_machines_detections_and_count_them(conn, compressor, types):
    """P3.5b: a trip on the compressor while a shutdown of it runs is counted under the shutdown, not opened; a
    safety type, a type that doesn't declare it, and another asset still open cases. A person's window ended early
    stops suppressing, and what it held back stays counted."""
    from fuzer.core import gates, plan
    from fuzer.core.findings import Finding
    from fuzer.ops import suppression

    types("trip_x", role="role:engineer", suppress=True)
    types("safety_x", role="role:engineer", suppress=True)
    gates.set_safety_case_types(conn, ["safety_x"], by="test")  # a safety case type: never suppressed
    types("report_x", role="role:engineer")
    shut = plan.propose(conn, "shutdown", "B1 shutdown", "test", "2026-11-10T00:00", "2026-11-12T00:00",
                        refs=[compressor["tag"]], status="scheduled")
    during = Finding("test", "deterministic", start="2026-11-11T03:10")
    a = raise_exception(conn, "trip_x", "T-1", "Train tripped", "trips", refs=[compressor["pt"]], finding=during)
    again = raise_exception(conn, "trip_x", "T-1", "Train tripped", "trips", refs=[compressor["pt"]], finding=during)
    assert a["state"] == again["state"] == "suppressed" and not a["item_id"]
    assert again["suppressed"]["kind"] == "shutdown" and again["suppressed"]["count"] == 2
    assert not conn.execute("select 1 from exception_record where type = 'trip_x'").fetchone()
    safety = raise_exception(conn, "safety_x", "I-1", "IOW", "limits", refs=[compressor["pt"]], finding=during)
    plain = raise_exception(conn, "report_x", "R-1", "Report", "sap", refs=[compressor["pt"]], finding=during)
    other = raise_exception(conn, "trip_x", "T-2", "Separator", "trips", refs=[compressor["elsewhere"]], finding=during)
    after = raise_exception(conn, "trip_x", "T-3", "Tripped again", "trips", refs=[compressor["pt"]],
                            finding=Finding("test", "deterministic", start="2026-11-12T08:00"))
    assert all(x["state"] == "open" for x in (safety, plain, other, after))
    found = suppression.found(conn)
    assert (found["findings"], found["raised"]) == (1, 2) and found["items"][0]["window_id"] == shut["id"]

    mine = suppression.add(conn, [compressor["tag"]], "2020-01-01T00:00", "2099-01-01T00:00", "Valve work", "ann")
    held = raise_exception(conn, "trip_x", "T-4", "Tripped", "trips", refs=[compressor["equipment"]])
    assert held["suppressed"]["kind"] == "person"
    assert any(w["kind"] == "person" and w["findings"] == 1 for w in suppression.windows(conn))
    suppression.end(conn, mine["id"], "ann")
    opened = raise_exception(conn, "trip_x", "T-5", "Tripped", "trips", refs=[compressor["equipment"]])
    assert opened["state"] == "open" and suppression.found(conn)["findings"] == 2
    conn.rollback()


def test_a_shelved_condition_leaves_the_list_and_returns_when_its_expiry_passes(conn, compressor, types):
    """P3.5c: shelved with a reason and an expiry, it leaves the open list and a repeat raise doesn't bring it back;
    once the expiry passes the work tick's reconcile returns it, and says so on its case. A safety type can't be
    shelved, nor can one be shelved without a reason, into the past or past the longest shelving."""
    from datetime import UTC, datetime, timedelta

    from fuzer.core import gates

    types("standing_x", role="role:engineer")
    types("safety_y", role="role:engineer")
    gates.set_safety_case_types(conn, ["safety_y"], by="test")
    x = raise_exception(conn, "standing_x", "S-1", "Seal weeping", "screen", refs=[compressor["tag"]])
    soon = datetime.now(UTC) + timedelta(days=2)
    with pytest.raises(ExceptionError, match="reason"):
        impl.shelve(conn, x["id"], "ann", " ", soon)
    with pytest.raises(ExceptionError, match="future"):
        impl.shelve(conn, x["id"], "ann", "known", datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(ExceptionError, match="at most"):
        impl.shelve(conn, x["id"], "ann", "known", datetime.now(UTC) + timedelta(days=400))
    impl.shelve(conn, x["id"], "ann", "Known weep, repair at the next outage", soon.isoformat())
    assert x["id"] not in [r["id"] for r in impl.listing(conn)]
    assert [r["id"] for r in impl.listing(conn, state="shelved")] == [x["id"]]
    again = raise_exception(conn, "standing_x", "S-1", "Seal weeping", "screen", refs=[compressor["tag"]])
    assert not again["raised"] and x["id"] not in [r["id"] for r in impl.listing(conn)]
    assert impl.reconcile(conn)["unshelved"] == 0

    conn.execute("update exception_record set shelved_until = now() - interval '1 minute' where id = %s", (x["id"],))
    assert impl.reconcile(conn)["unshelved"] == 1
    assert x["id"] in [r["id"] for r in impl.listing(conn)]
    notes = [r["data"]["text"] for r in conn.execute(
        "select data from work_event where item_id = %s and kind = 'commented' order by id", (x["item_id"],))]
    assert notes[0].startswith("Shelved until") and notes[-1].startswith("Back on the list: its shelving expired")

    y = raise_exception(conn, "safety_y", "Y-1", "IOW", "limits", refs=[compressor["elsewhere"]])
    with pytest.raises(ExceptionError, match="safety"):
        impl.shelve(conn, y["id"], "ann", "known", soon)
    conn.rollback()


def test_joining_can_be_turned_off(conn, compressor, three_sources):
    from fuzer.core import settings

    settings.set(conn, "exceptions.join_hours", 0, by="test")
    try:
        sap, iow, _ = _raise_trip(conn, compressor, 3)
        assert not iow.get("joined") and iow["item_id"] != sap["item_id"]
    finally:
        settings.set(conn, "exceptions.join_hours", 48, by="test")
    conn.commit()


def test_the_event_is_dispatched_reported_and_closed_once(conn, compressor, three_sources):
    from fuzer.core import work
    from fuzer.core.exceptions import reconcile

    sap, iow, sre = _raise_trip(conn, compressor, 4)
    sent = impl.dispatch(conn, iow["id"], "ioc-ann", "fred", reason="check the compressor")  # from any source's row
    assert sent["id"] == sap["id"]
    assert [j["id"] for j in impl.field_jobs(conn, "fred") if j["item_id"] == sap["item_id"]] == [sap["id"]]
    [job] = [j for j in impl.field_jobs(conn, "fred") if j["item_id"] == sap["item_id"]]
    assert len(job["sources"]) == 3 and {a["fuzer_id"] for a in job["assets"]} >= {compressor["point"]}
    said = impl.report(conn, sre["id"], "fred", "note", note="bearing hot to touch")
    assert said["id"] == sap["id"] and said["evidence"]["field"][-1]["note"] == "bearing hot to touch"
    work.close_case(conn, sap["item_id"], "done", "ann")
    reconcile(conn)
    states = {r["id"]: r["state"] for r in conn.execute(
        "select id, state from exception_record where id = any(%s)", ([sap["id"], iow["id"], sre["id"]],))}
    assert set(states.values()) == {"closed"}
    conn.commit()


def test_a_source_that_clears_leaves_the_case_with_the_others(conn, compressor, three_sources):
    sap, iow, sre = _raise_trip(conn, compressor, 5)
    clear_exception(conn, "iow_hi", "IOW-5", "limits")  # a joined source clears: nothing else changes
    assert conn.execute("select state from work_item where id = %s", (sap["item_id"],)).fetchone()["state"] != "done"
    clear_exception(conn, "sap_note", "N-5", "sap", reason="notification closed")  # the host clears
    assert conn.execute("select state from work_item where id = %s", (sap["item_id"],)).fetchone()["state"] != "done"
    [row] = [r for r in impl.listing(conn) if r["item_id"] == sap["item_id"]]
    assert row["id"] == sre["id"]  # still holding: it carries the case now, the cleared ones under it
    assert sorted((j["id"], j["state"]) for j in row["joined"]) == [(sap["id"], "cleared"), (iow["id"], "cleared")]
    clear_exception(conn, "upset_x", "U-5", "sre")
    assert conn.execute("select state from work_item where id = %s", (sap["item_id"],)).fetchone()["state"] == "done"
    conn.commit()


def test_the_ioc_splits_a_source_that_was_a_different_event(conn, compressor, three_sources):
    sap, iow, _ = _raise_trip(conn, compressor, 6)
    with pytest.raises(ExceptionError, match="isn't part of another's case"):
        impl.split(conn, sap["id"], "ann")
    out = impl.split(conn, iow["id"], "ann", reason="instrument drift, not the trip")
    assert out["joined_to"] is None and out["item_id"] and out["item_id"] != sap["item_id"]
    assert conn.execute("select role from work_item where id = %s", (out["item_id"],)).fetchone()["role"] == "engineer"
    note = conn.execute("select data->>'text' as t from work_event where item_id = %s and kind = 'commented' "
                        "order by id desc limit 1", (sap["item_id"],)).fetchone()["t"]
    assert note.startswith("Split out: iow_hi: Inlet pressure IOW breach (instrument drift, not the trip)")
    conn.commit()


def test_a_waiting_exception_joins_the_case_another_source_opens(conn, compressor, three_sources, types):
    """An SAP notification waits on Exceptions to be dispatched (no case). When the IOW check opens a case on the same
    compressor, the notification joins it; two notifications both waiting join when the IOC dispatches one."""
    types("note_waiting")  # no queue: waits to be dispatched
    waiting = raise_exception(conn, "note_waiting", "W-1", "Vibration trip", "sap", refs=[compressor["equipment"]])
    assert waiting["item_id"] is None
    iow = raise_exception(conn, "iow_hi", "IOW-7", "Inlet pressure IOW breach", "limits", refs=[compressor["point"]])
    row = conn.execute("select joined_to, item_id from exception_record where id = %s", (waiting["id"],)).fetchone()
    assert row["joined_to"] == iow["id"] and row["item_id"] == iow["item_id"]
    impl.split(conn, waiting["id"], "ann")  # its type routes nowhere: split out, it waits again
    assert conn.execute("select item_id from exception_record where id = %s", (waiting["id"],)).fetchone()["item_id"] is None
    clear_exception(conn, "iow_hi", "IOW-7", "limits")  # the breach is over, its case closes
    other = raise_exception(conn, "note_waiting", "W-2", "Seal leak", "sap", refs=[compressor["pt"]])
    sent = impl.dispatch(conn, other["id"], "ann", "fred")
    row = conn.execute("select joined_to, item_id from exception_record where id = %s", (waiting["id"],)).fetchone()
    assert row["joined_to"] == other["id"] and row["item_id"] == sent["item_id"]
    conn.commit()


@pytest.mark.unit
def test_an_sap_notification_reads_as_words_in_the_field():
    """SAP status and type, and the ISO 14224 failure mode and mechanism, read as words with the code after them;
    the site's damage and cause codes they map from aren't repeated (#494)."""
    from fuzer.core.findings import shown
    from fuzer.ops.intake import finding

    def read(f):
        return dict(pairs({"finding": shown(finding("10004711", f).evidence())}))

    assert read({"notif_type": "M1", "status": "OSNO", "failure_mode": "VIB", "failure_mechanism": "1.2",
                 "damage_code": "D01", "cause_code": "C02", "priority": "2"}) == {
        "Notification": "10004711", "Notification type": "maintenance request (M1)", "Status": "outstanding (OSNO)",
        "SAP priority": "2", "Failure mode": "Vibration (VIB)", "Failure mechanism": "Vibration (1.2)"}
    assert read({"status": "ZZZZ", "damage_code": "DM-LEAK"}) == {
        "Notification": "10004711", "Status": "ZZZZ", "Damage code": "DM-LEAK"}
    assert finding("10004711", {"status": "OSNO"}).record()["origin"] == "external"


def test_a_dispatched_sap_notification_is_due_by_its_priority(conn):
    """SAP priority 1 is due in a day, from when Fuzer raised it (#494); the setting can turn it off."""
    from fuzer.core import settings
    from fuzer.ops import exceptions as ex
    from fuzer.ops import intake  # noqa: F401  (declares sap_notification)

    e = raise_exception(conn, "sap_notification", "N-DUE-1", "Seal leak (notification N-DUE-1)", "sap",
                        refs=["tag:NO-SUCH-TAG"], finding=external("test", {"priority": "1", "status": "OSNO"}))  # matches nothing: joins no case
    row = ex.dispatch(conn, e["id"], "ivy", "role:operator", reason="Check the seal")
    due = conn.execute("select due from work_item where id = %s", (row["item_id"],)).fetchone()["due"]
    assert round((due - row["first_seen"]).total_seconds() / 3600) == 24
    settings.set(conn, "intake.sap_notifications.due_hours", "", by="test")
    try:
        assert ex._due(conn, row) is None
    finally:
        settings.reset(conn, "intake.sap_notifications.due_hours", by="test")
        # over: close it
        conn.execute("update work_item set state = 'done' where id = %s", (row["item_id"],))
        conn.execute("update exception_record set state = 'closed' where id = %s", (e["id"],))
    conn.commit()


# ---- one front door for work: the routing table and explain (#509) --------------------------------------------------

def _counts(conn) -> tuple:
    return tuple(conn.execute(f"select count(*) as n from {t}").fetchone()["n"]
                 for t in ("exception_record", "work_item", "work_event", "work_process"))


def test_the_routing_table_lists_every_type_with_what_it_starts(conn, types, monkeypatch):
    from fuzer.ops import intake, routing  # noqa: F401  (intake declares sap_notification)

    types("r_queue", role="role:maintenance")
    types("r_proc", process="exc_proc")
    types("r_board", joins=False)
    impl.define(conn, "r_pack", "test", "Pack type", role="role:engineer", source_system="kestrel_scada")
    table = {r["type"]: r for r in routing.routes(conn)}
    assert (table["r_queue"]["starts"], table["r_queue"]["role"]) == ("queue", "maintenance")
    assert (table["r_proc"]["starts"], table["r_proc"]["process"]) == ("process", "exc_proc")
    assert table["r_board"]["starts"] == "dispatch" and table["r_board"]["join_hours"] is None
    assert table["r_queue"]["declared_in"] == "code" and table["r_queue"]["join_hours"] == 48
    assert (table["r_pack"]["declared_in"], table["r_pack"]["source_system"]) == ("source pack", "kestrel_scada")
    due = table["sap_notification"]["due"]
    assert due["by"] == "priority" and due["hours"]["1"] == 24 and due["applies"] == "when dispatched"
    assert table["r_queue"]["due"] is None
    conn.rollback()


def test_explain_says_where_work_goes_and_who_holds_it_without_writing(conn, types):
    from fuzer.ops import routing
    from fuzer.workflow import engine

    engine.set_person(conn, "mia", ["operator"])
    types("r_queue", role="role:operator")
    types("r_board")
    conn.commit()
    before = _counts(conn)
    queued = routing.explain(conn, "r_queue", refs=["tag:P-101", "tag:NOPE"], key="Q-1")
    assert queued["outcome"] == "queue" and queued["queue"]["role"] == "operator" and "mia" in queued["queue"]["holders"]
    assert [m["id"] for m in queued["matched"]] == ["fz-pump-1"] and queued["unmatched"] == ["tag:NOPE"]
    assert "operator queue (" in queued["summary"] and "mia" in queued["summary"]
    waits = routing.explain(conn, "r_board", key="B-1")
    assert waits["outcome"] == "dispatch" and "dispatch" in waits["summary"]
    sent = routing.explain(conn, "r_board", key="B-1", to="role:operator")
    assert sent["outcome"] == "dispatched" and sent["queue"]["role"] == "operator"
    assert _counts(conn) == before  # an explanation leaves nothing behind
    with pytest.raises(ExceptionError, match="no exception type"):
        routing.explain(conn, "no_such_type")


def test_explain_names_the_process_and_each_lane(conn, types):
    from fuzer.ops import routing
    from fuzer.workflow import engine

    engine.set_person(conn, "olga", ["engineer", "workflow_admin"])
    if not conn.execute("select 1 from work_definition where process_key = 'exc_proc' and state = 'published'").fetchone():
        engine.publish(conn, engine.import_bpmn(conn, _process("exc_proc"), "olga")[0]["id"], "olga")
    types("r_proc", process="exc_proc")
    types("r_unpublished", process="never_published")
    conn.commit()
    out = routing.explain(conn, "r_proc", key="P-1")
    assert out["outcome"] == "process" and out["process"]["published"]
    [step] = out["process"]["steps"]
    assert (step["step"], step["role"], step["sla"]) == ("Look", "engineer", "P1D") and "olga" in step["holders"]
    assert "first person step, Look, waits in the engineer queue" in out["summary"]
    missing = routing.explain(conn, "r_unpublished", key="P-1")
    assert not missing["process"]["published"] and "no version of it is published" in missing["summary"]


def test_explain_finds_the_open_exception_and_the_event_it_joins(conn, compressor, three_sources):
    from fuzer.ops import routing

    sap, _, _ = _raise_trip(conn, compressor, 9)
    again = routing.explain(conn, "sap_note", refs=[compressor["equipment"]], key="N-9")
    assert again["outcome"] == "updates" and again["updates"]["exception_id"] == sap["id"]
    joins = routing.explain(conn, "iow_hi", refs=[compressor["point"]], key="IOW-NEW")
    assert joins["outcome"] == "joins" and joins["joins"]["case"]["id"] == sap["item_id"]
    assert f"Joins case {sap['item_id']}" in joins["summary"]
    elsewhere = routing.explain(conn, "iow_hi", refs=[compressor["elsewhere"]], key="IOW-V2")
    assert elsewhere["outcome"] == "queue"  # same area is not the same event


def test_any_type_can_carry_a_due_policy(conn):
    """The due policy is a field of every type (#509): a type defined as data with due_hours is due by its priority
    when dispatched, as SAP notifications are; a bad map is refused."""
    from fuzer.ops import exceptions as ex
    from fuzer.ops import routing

    with pytest.raises(ex.ExceptionError, match="priority to hours"):
        ex.define(conn, "leak_report", "test", "Leak report", due_hours="soon")
    ex.define(conn, "leak_report", "test", "Leak report", due_hours="1:4, 3:48")
    conn.commit()
    try:
        assert routing.route_for(conn, "leak_report")["due"] == {
            "by": "priority", "hours": {"1": 4.0, "3": 48.0}, "setting": None, "from": "raised",
            "applies": "when dispatched"}
        e = raise_exception(conn, "leak_report", "LR-1", "Drip at the manifold", "walkdown",
                            refs=["tag:NO-SUCH-TAG"], finding=external("test", {"priority": "1"}))
        row = ex.dispatch(conn, e["id"], "ivy", "role:operator", reason="Have a look")
        due = conn.execute("select due from work_item where id = %s", (row["item_id"],)).fetchone()["due"]
        assert round((due - row["first_seen"]).total_seconds() / 3600) == 4
        assert routing.route_for(conn, "sap_notification")["due"]["setting"] == "intake.sap_notifications.due_hours"
    finally:
        conn.execute("update work_item set state = 'done' where type = 'leak_report'")
        conn.execute("update exception_record set state = 'closed' where type = 'leak_report'")
        conn.commit()


def test_explain_gives_a_dispatched_sap_notification_its_due_time(conn):
    from fuzer.ops import intake, routing  # noqa: F401  (intake declares sap_notification)

    out = routing.explain(conn, "sap_notification", key="N-EXPLAIN", to="fred", evidence={"priority": "1"})
    assert out["outcome"] == "dispatched" and out["queue"]["assignee"] == "fred" and out["due"]


def test_exceptions_open_cases_only_through_the_front_door():
    """Every case an exception opens goes through routing.route (#509): the exception module calls the Work port
    only to dispatch, feed or comment on a case that exists."""
    src = (PACKAGE / "ops" / "exceptions.py").read_text()
    assert not re.search(r"work\.(open_case|start_or_correlate)\(", src)


def test_a_queue_type_runs_as_the_queue_item_process(conn, types):
    """A type that names a queue and no process starts the standard queue_item process (#509): one step in that
    queue with the role's SLA and escalation, the case typed as the type's case. Clearing the condition or the
    field's Done still closes it, as it did a plain queue case; Could not do hands the step back."""
    from fuzer.core.exceptions import clear_exception
    from fuzer.ops import exceptions as ex
    from fuzer.ops import routing

    types("gas_day_short", role="role:operator", case_type="measurement")
    e = raise_exception(conn, "gas_day_short", "GD-1", "Gas day short", "kestrel", refs=["tag:NOPE-1"])
    proc = conn.execute("select process_key, state from work_process where item_id = %s", (e["item_id"],)).fetchone()
    assert proc["process_key"] == "queue_item" and proc["state"] == "running"
    case = conn.execute("select type, role, due, payload from work_item where id = %s", (e["item_id"],)).fetchone()
    assert case["type"] == "measurement" and case["role"] == "operator" and case["due"] is not None  # the role's SLA
    step = conn.execute("select role, name from work_token where item_id = %s and kind = 'user' and state = 'waiting'",
                        (e["item_id"],)).fetchone()
    assert (step["role"], step["name"]) == ("operator", "Work the case")
    assert routing.queue_case(conn, e["item_id"])
    cleared = clear_exception(conn, "gas_day_short", "GD-1", "kestrel", reason="the day was corrected")
    assert cleared["state"] == "cleared"
    assert conn.execute("select state from work_item where id = %s", (e["item_id"],)).fetchone()["state"] == "done"
    assert conn.execute("select state from work_process where item_id = %s", (e["item_id"],)).fetchone()["state"] == "completed"

    f = raise_exception(conn, "gas_day_short", "GD-2", "Gas day short again", "kestrel", refs=["tag:NOPE-2"])
    ex.dispatch(conn, f["id"], "ioc-ann", "fred", reason="check the meter")
    back = ex.report(conn, f["id"], "fred", "could_not_do", note="meter room locked")
    step = conn.execute("select assignee, role from work_token where item_id = %s and kind = 'user' and state = 'waiting'",
                        (f["item_id"],)).fetchone()
    assert back["state"] == "open" and step["assignee"] != "fred"  # back with the IOC, not left with the operator
    ex.dispatch(conn, f["id"], "ioc-ann", "fred", reason="key is at the gate")
    done = ex.report(conn, f["id"], "fred", "done", note="meter reset")
    assert done["state"] == "closed"
    assert conn.execute("select state from work_item where id = %s", (f["item_id"],)).fetchone()["state"] == "done"

    from fuzer.workflow import engine

    engine.set_person(conn, "fred", ["operator"])
    types("leak", audience=("ioc", "field"))
    w = raise_exception(conn, "leak", "walk-QI", "Leak at the meter run", "olga", refs=["tag:NOPE-4"])
    assert w["item_id"] is None  # no queue: waits for the IOC
    explained = routing.explain(conn, "leak", key="walk-QI2", to="fred")
    assert explained["process"]["key"] == "queue_item" and "hands it to fred" in explained["summary"]
    sent = ex.dispatch(conn, w["id"], "ioc-ann", "fred", reason="nearest operator")
    assert routing.queue_case(conn, sent["item_id"])  # a dispatch runs as queue_item too (#509)
    step = conn.execute("select assignee, role, due_at from work_token where item_id = %s and kind = 'user' "
                        "and state = 'waiting'", (sent["item_id"],)).fetchone()
    assert step["assignee"] == "fred" and step["due_at"] is not None
    ex.report(conn, w["id"], "fred", "could_not_do", note="no access")
    assert conn.execute("select assignee from work_token where item_id = %s and kind = 'user' and state = 'waiting'",
                        (sent["item_id"],)).fetchone()["assignee"] == "ioc-ann"  # back to whoever sent it

    types("odd_queue", role="role:not_a_person_role")  # a queue no person role backs stays a plain case
    g = raise_exception(conn, "odd_queue", "OQ-1", "Odd", "kestrel", refs=["tag:NOPE-3"])
    assert not routing.queue_case(conn, g["item_id"])
    assert routing.explain(conn, "gas_day_short", key="GD-3")["process"]["key"] == "queue_item"
    conn.commit()


def test_every_module_that_declares_a_type_is_loaded_with_the_routing_table():
    from fuzer.core import exceptions as port

    declared = set()
    for f in PACKAGE.rglob("*.py"):
        if re.search(r"^\s+return TYPES\.get\(\"\w+\"\) or exception_type\(|^[A-Z_]+ = exception_type\(|^exception_type\("
                     r"|^[A-Z_]+ = \{\w+: exception_type\(",
                     f.read_text(), re.MULTILINE):
            declared.add("fuzer." + ".".join(f.relative_to(PACKAGE).with_suffix("").parts))
    assert declared == set(port.DECLARED_IN)
