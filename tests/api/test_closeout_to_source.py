"""Close-out goes back to the source (#466): an SAP notification the field closes drafts the SAP update on the
maintenance planner's Inbox, with the field's note in it. Approving it marks the notification done in the demo's
SAP data, so the next intake no longer holds it, and Could not do drafts a follow-up notification instead."""

import csv
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_closeout"


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _admin():
    return db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres"))


def _fresh_caches():
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def _sap(path: Path) -> dict[str, dict]:
    with path.open(newline="") as f:
        return {r["qmnum"]: r for r in csv.DictReader(f)}


def test_the_field_closing_a_notification_drafts_the_sap_update(monkeypatch, tmp_path):
    from fuzer import api
    from fuzer.ops import pipeline
    from fuzer.sources import ingest

    bundle = tmp_path / "kestrel"
    shutil.copytree(KESTREL, bundle)  # the demo's SAP data is changed by the test
    with _admin() as a:
        a.execute(f'drop database if exists "{NAME}" with (force)')
        a.execute(f'create database "{NAME}"')
    url = make_conninfo(db.database_url(), dbname=NAME)
    monkeypatch.setenv("FUZER_DATABASE_URL", url)
    monkeypatch.delenv("FUZER_PROJECT", raising=False)
    monkeypatch.setattr(db, "_default", db._default)
    _fresh_caches()
    sap = bundle / "exports" / "sap_notifications.csv"
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            first = bundles.init(conn, bundle, by="test")
            data = Path(first["data"])
            monkeypatch.setenv("FUZER_DATA_DIR", str(data))  # where this project's exports are, as the server has it
            settings.set(conn, "closeout.apply_to_exports", True, by="test")
            from fuzer.workflow import engine

            engine.set_person(conn, "pat", ["maintenance_planner"])  # the planner who approves SAP write-backs
            conn.commit()
            pack = packs.current_pack(conn)

            def refresh():
                ingest.ingest(conn, data)
                pipeline.rebuild(conn, pack)
                out = pipeline.follow_register(conn, pack)
                conn.commit()
                return out["notifications"]

            refresh()
            raised = {r["key"]: r for r in conn.execute(
                "select * from exception_record where type = 'sap_notification' and state = 'open'")}
            assert {"10004002", "10004007"} <= set(raised)

            with TestClient(api.app) as client:
                # done in the field: the planner gets the SAP completion to approve, with the field's note
                done = raised["10004002"]
                ok(client.post(f"/exceptions/{done['id']}/dispatch", json={"by": "ioc-ann", "to": "fred"}))
                ok(client.post(f"/exceptions/{done['id']}/report",
                               json={"by": "fred", "status": "done", "note": "repacked the gland, no leak at 30 min"}))
                [draft] = ok(client.get("/closeouts", params={"exception_id": done["id"]}))
                assert draft["state"] == "draft" and draft["action"] == "complete" and draft["record"] == "10004002"
                assert "repacked the gland" in draft["draft"]["text"] and draft["draft"]["status"] == "NOCO"
                case = conn.execute("select * from work_item where id = %s", (draft["item_id"],)).fetchone()
                assert case["role"] == "maintenance_planner" and case["state"] == "open"
                assert case["type"] == "source_closeout" and case["payload"]["start"]["closeout_id"] == draft["id"]
                assert conn.execute("select process_key from work_process where item_id = %s",  # its role's SLA (#509)
                                    (draft["item_id"],)).fetchone()["process_key"] == "source_closeout"
                conn.rollback()

                # back with the IOC (#1115), which closes it out; then the field's word holds it until SAP moves
                job = conn.execute("select item_id from exception_record where id = %s", (done["id"],)).fetchone()
                closed = ok(client.post(f"/work/items/{job['item_id']}/closeout", json={
                    "by": "ioc-ann", "disposition": "real", "failure_mode": "ELP", "cause": "3", "activity": "2"}))
                # with the case as it now stands, closed, for the page to drop Waiting on at once (tour F12)
                assert closed["item"]["id"] == job["item_id"] and closed["item"]["closed_at"], closed["item"]
                conn.rollback()
                assert refresh()["held"] == 1
                assert _sap(sap)["10004002"]["status"] == "OSNO"

                # the change in plain words, and the step is decided by approving or sending it back, never by a
                # bare Complete that would end the case with nothing written (#1145)
                assert ok(client.get(f"/closeouts/{draft['id']}"))["change"] == \
                    "Close notification 10004002 in SAP (status NOCO, notification completed), with the note below."
                step = conn.execute("select t.id from work_token t join work_process p on p.id = t.process_id "
                                    "where p.item_id = %s and t.state = 'waiting'", (draft["item_id"],)).fetchone()["id"]
                conn.rollback()
                r = client.post(f"/work/tasks/{step}/complete", json={"by": "pat", "data": {}})
                assert r.status_code == 409 and "send it back" in r.text, r.text
                assert client.post(f"/closeouts/{draft['id']}/send-back", json={"by": "pat"}).status_code == 400
                # an agent can't approve a write-back (P5.10 fixed gate), nor someone outside the approver roles
                assert client.post(f"/closeouts/{draft['id']}/approve", json={"by": "agent:maintenance"}).status_code == 403
                sent = ok(client.post(f"/closeouts/{draft['id']}/approve",
                                      json={"by": "pat", "text": "Gland repacked in the field; no leak after 30 min."}))
                assert sent["state"] == "approved" and sent["draft"]["text"].startswith("Gland repacked")
                again = client.post(f"/closeouts/{draft['id']}/approve", json={"by": "pat"})
                assert again.status_code == 400 and "not a draft" in again.text
                # approved, it waits for SAP's next batch (P5.7): the case is released once SAP has it
                case = conn.execute("select state from work_item where id = %s", (draft["item_id"],)).fetchone()
                assert case["state"] == "open"
                [queued] = ok(client.get("/writebacks", params={"item_id": draft["item_id"]}))
                assert queued["state"] == "approved" and queued["approved_by"] == "pat"
                conn.rollback()

                # could not do: a follow-up notification is drafted instead
                cnd = raised["10004007"]
                # the reboiler's hot work (PTW-24105, its order under way today) clashes with this line break (#715): a real
                # SIMOPS block every time, so the dispatch says why it goes anyway
                r = client.post(f"/exceptions/{cnd['id']}/dispatch", json={"by": "ioc-ann", "to": "fred"})
                assert r.status_code == 409 and "Hot work near a line break in KRGP-C1" in r.text, r.text
                ok(client.post(f"/exceptions/{cnd['id']}/dispatch", json={"by": "ioc-ann", "to": "fred", "override":
                    "contactor isolated and gas-free before the reboiler hot work restarts; area authority agreed"}))
                ok(client.post(f"/exceptions/{cnd['id']}/report",
                               json={"by": "fred", "status": "could_not_do", "note": "needs a lab sample first"}))
                [follow] = ok(client.get("/closeouts", params={"exception_id": cnd["id"]}))
                assert follow["action"] == "follow_up" and "needs a lab sample first" in follow["draft"]["text"]
                # sent back: nothing goes to SAP, and the job's case hears why
                fred_job = conn.execute("select item_id from exception_record where id = %s", (cnd["id"],)).fetchone()
                conn.rollback()
                assert client.post(f"/closeouts/{follow['id']}/send-back",
                                   json={"by": "fred", "reason": "no"}).status_code == 400  # not an approver
                back = ok(client.post(f"/closeouts/{follow['id']}/send-back",
                                      json={"by": "pat", "reason": "the lab sample is booked; raise it after"}))
                assert back["state"] == "discarded"
                said = conn.execute("select data->>'text' as t from work_event where item_id = %s and kind = 'commented' "
                                    "order by id desc limit 1", (fred_job["item_id"],)).fetchone()["t"]
                assert said == "Sent back, not written to SAP: the lab sample is booked; raise it after"
                conn.rollback()
                # the IOC sends the field out again, and this time the follow-up is approved
                ok(client.post(f"/exceptions/{cnd['id']}/dispatch", json={"by": "ioc-ann", "to": "fred", "override":
                    "contactor isolated and gas-free before the reboiler hot work restarts; area authority agreed"}))
                ok(client.post(f"/exceptions/{cnd['id']}/report",
                               json={"by": "fred", "status": "could_not_do", "note": "needs a lab sample first"}))
                [follow] = ok(client.get("/closeouts", params={"exception_id": cnd["id"], "state": "draft"}))
                ok(client.post(f"/closeouts/{follow['id']}/approve", json={"by": "pat"}))
                conn.rollback()

            from datetime import datetime, timedelta

            from fuzer.ops import writeback

            writeback.run(conn, datetime.now().astimezone() + timedelta(hours=1))  # the batch: the demo's SAP adapter
            applied = conn.execute("select id, action, applied_at, applied_note from source_closeout "
                                   "where state <> 'discarded' order by id").fetchall()
            assert all(r["applied_at"] for r in applied), (applied, str(sap), conn.execute(
                "select id, state, error from writeback").fetchall())
            assert {r["state"] for r in conn.execute("select state from writeback")} == {"confirmed", "rejected"}
            assert conn.execute("select state from work_item where id = %s", (draft["item_id"],)).fetchone()["state"] == "done"
            assert {r["applied_note"] for r in applied} == {str(sap)}
            notes = _sap(sap)
            assert notes["10004002"]["status"] == "NOCO"
            new = [n for q, n in notes.items() if q not in ("10004001", "10004002", "10004003", "10004004", "10004005",
                                                           "10004006", "10004007", "10004008", "10004009", "10004010",
                                                           "10004011", "10004012")]
            assert len(new) == 1 and new[0]["status"] == "OSNO" and "needs a lab sample first" in new[0]["short_text"]
            assert new[0]["equnr"] == notes["10004007"]["equnr"]

            # the next intake sees SAP move: no longer held, and the follow-up is raised for the IOC
            out = refresh()
            assert out["held"] == 0
            assert conn.execute("select value from golden g join entity_key k on k.fuzer_id = g.fuzer_id "
                                "where k.kind = 'notification' and k.key = '10004002' and g.field = 'status'"
                                ).fetchone()["value"] == "NOCO"
            reraised = {r["key"] for r in conn.execute(
                "select key from exception_record where type = 'sap_notification' and state = 'open'")}
            assert new[0]["qmnum"] in reraised and "10004002" not in reraised
    finally:
        _fresh_caches()
        with _admin() as a:
            a.execute(f'drop database if exists "{NAME}" with (force)')
