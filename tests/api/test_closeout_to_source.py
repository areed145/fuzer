"""Close-out goes back to the source (#466): an SAP notification the field closes drafts the SAP update on the
maintenance planner's Inbox, with the field's note in it. Approving it marks the notification done in the demo's
SAP data, so the next intake no longer holds it, and Could not do drafts a follow-up notification instead."""

import csv
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, events, settings
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
                assert case["type"] == "source_closeout" and case["payload"]["closeout_id"] == draft["id"]
                conn.rollback()

                # the field's word holds it until SAP moves
                assert refresh()["held"] == 1
                assert _sap(sap)["10004002"]["status"] == "OSNO"

                sent = ok(client.post(f"/closeouts/{draft['id']}/approve",
                                      json={"by": "pat", "text": "Gland repacked in the field; no leak after 30 min."}))
                assert sent["state"] == "sent" and sent["draft"]["text"].startswith("Gland repacked")
                again = client.post(f"/closeouts/{draft['id']}/approve", json={"by": "pat"})
                assert again.status_code == 400 and "not a draft" in again.text
                case = conn.execute("select state from work_item where id = %s", (draft["item_id"],)).fetchone()
                assert case["state"] == "done"
                conn.rollback()

                # could not do: a follow-up notification is drafted instead
                cnd = raised["10004007"]
                ok(client.post(f"/exceptions/{cnd['id']}/dispatch", json={"by": "ioc-ann", "to": "fred"}))
                ok(client.post(f"/exceptions/{cnd['id']}/report",
                               json={"by": "fred", "status": "could_not_do", "note": "needs a lab sample first"}))
                [follow] = ok(client.get("/closeouts", params={"exception_id": cnd["id"]}))
                assert follow["action"] == "follow_up" and "needs a lab sample first" in follow["draft"]["text"]
                ok(client.post(f"/closeouts/{follow['id']}/approve", json={"by": "pat"}))
                conn.rollback()

            events.deliver(conn)  # the demo's SAP adapter
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
