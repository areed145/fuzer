"""Shift handover (#467): at a shift change a handover is drafted from live work with five sections, each line
citing its case or exception; the incoming ops lead sees it on their Inbox and acknowledges it, who and when kept
on the record; past handovers are listed and readable."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_handover"


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _admin():
    return db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres"))


def _fresh_caches():
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def test_a_handover_is_drafted_at_the_shift_change_and_acknowledged(monkeypatch):
    from fuzer import api
    from fuzer.ops import handover, pipeline
    from fuzer.sources import ingest

    with _admin() as a:
        a.execute(f'drop database if exists "{NAME}" with (force)')
        a.execute(f'create database "{NAME}"')
    url = make_conninfo(db.database_url(), dbname=NAME)
    monkeypatch.setenv("FUZER_DATABASE_URL", url)
    monkeypatch.delenv("FUZER_PROJECT", raising=False)
    monkeypatch.setattr(db, "_default", db._default)
    _fresh_caches()
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            first = bundles.init(conn, KESTREL, by="test")
            pack = packs.current_pack(conn)
            ingest.ingest(conn, Path(first["data"]))
            pipeline.rebuild(conn, pack)
            pipeline.follow_register(conn, pack)
            settings.set(conn, "handover.shift_times", "00:00,12:00", by="test")
            conn.commit()
            raised = {r["key"]: r for r in conn.execute(
                "select * from exception_record where type = 'sap_notification' and state = 'open'")}

            with TestClient(api.app) as client:
                sent, back = raised["10004002"], raised["10004007"]
                ok(client.post(f"/exceptions/{sent['id']}/dispatch", json={"by": "ioc-ann", "to": "fred",
                                                                          "reason": "check the packing"}))
                # the reboiler's hot work (PTW-24105, its order under way today) clashes with this line break (#715): a real
                # SIMOPS block every time, so the dispatch says why it goes anyway
                r = client.post(f"/exceptions/{back['id']}/dispatch", json={"by": "ioc-ann", "to": "fred"})
                assert r.status_code == 409 and "Hot work near a line break in KRGP-C1" in r.text, r.text
                ok(client.post(f"/exceptions/{back['id']}/dispatch", json={"by": "ioc-ann", "to": "fred", "override":
                    "contactor isolated and gas-free before the reboiler hot work restarts; area authority agreed"}))
                ok(client.post(f"/exceptions/{back['id']}/report", json={"by": "fred", "status": "could_not_do",
                                                                        "note": "needs a lab sample first"}))

                # the next shift change passes: the work tick drafts the handover once
                later = datetime.now(UTC) + timedelta(hours=12)
                h = handover.at_shift_change(conn, later)
                assert h is not None and handover.at_shift_change(conn, later) is None
                assert h["shift_end"] - h["shift_start"] == timedelta(hours=12)
                assert h["shift_start"] <= datetime.now(UTC) < h["shift_end"]
                conn.commit()

                s = h["facts"]["sections"]
                assert list(s) == handover.SECTIONS
                assert all(line["cites"] for lines in s.values() for line in lines)  # every line cites its source
                open_work = " ".join(x["text"] for x in s["Open work"])
                assert "with fred" in open_work
                [b] = [x for x in s["Back from the field"] if f"exception:{back['id']}" in x["cites"]]
                assert "fred reported could not do (needs a lab sample first)" in b["text"]
                dispatched = {c for x in s["Dispatched"] for c in x["cites"]}
                assert {f"exception:{sent['id']}", f"exception:{back['id']}"} <= dispatched
                assert any("sent to fred" in x["text"] and "check the packing" in x["text"] for x in s["Dispatched"])
                new = {c for x in s["New exceptions"] for c in x["cites"]}
                assert f"exception:{sent['id']}" in new

                # it is a case on the ops lead queue, and the incoming lead's Inbox has it
                case = conn.execute("select * from work_item where id = %s", (h["item_id"],)).fetchone()
                assert case["type"] == "shift_handover" and case["role"] == "ops_lead" and case["state"] == "open"
                assert case["due"] is not None  # the shift_handover process: the ops lead's SLA (#509)
                conn.rollback()

                ok(client.post(f"/handovers/{h['id']}/notes", json={"by": "lead-out", "notes": "K-2001 restart at 07:00"}))
                acked = ok(client.post(f"/handovers/{h['id']}/acknowledge", json={"by": "lead-in"}))
                assert acked["state"] == "acknowledged" and acked["acknowledged_by"] == "lead-in"
                assert acked["acknowledged_at"] and acked["notes"] == "K-2001 restart at 07:00"
                again = client.post(f"/handovers/{h['id']}/acknowledge", json={"by": "lead-in"})
                assert again.status_code == 400 and "already acknowledged by lead-in" in again.text
                history = {(r["actor"], r["data"].get("text")) for r in conn.execute(
                    "select actor, data from work_event where item_id = %s and kind = 'commented'", (h["item_id"],))}
                assert ("lead-in", "Acknowledged the handover") in history
                assert ("lead-out", "Outgoing notes: K-2001 restart at 07:00") in history
                case = conn.execute("select state from work_item where id = %s", (h["item_id"],)).fetchone()
                assert case["state"] == "done"
                conn.rollback()

                listed = ok(client.get("/handovers"))
                assert listed[0]["id"] == h["id"] and listed[0]["acknowledged_by"] == "lead-in"
                assert ok(client.get(f"/handovers/{h['id']}"))["facts"]["sections"]["Dispatched"]
                assert ok(client.get("/handovers/current"))["id"] == h["id"]
    finally:
        _fresh_caches()
        with _admin() as a:
            a.execute(f'drop database if exists "{NAME}" with (force)')
