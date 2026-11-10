"""Master data stewardship, resolving in place (#476): an exception whose ref the register doesn't know is matched
from the Exceptions page; remembering it records an alias, so the next exception with that ref matches on its own
(and a rebuild's resolvers know it too); one no case works yet joins the open case about that asset; and the
decision log lists the alias and reverses it."""

from pathlib import Path

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_steward"


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _admin():
    return db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres"))


def test_an_unmatched_ref_is_resolved_in_place_and_remembered(monkeypatch):
    from fuzer import api
    from fuzer.ops import pipeline
    from fuzer.sources import ingest, rules

    with _admin() as a:
        a.execute(f'drop database if exists "{NAME}" with (force)')
        a.execute(f'create database "{NAME}"')
    url = make_conninfo(db.database_url(), dbname=NAME)
    monkeypatch.setenv("FUZER_DATABASE_URL", url)
    monkeypatch.delenv("FUZER_PROJECT", raising=False)
    monkeypatch.setattr(db, "_default", db._default)
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            first = bundles.init(conn, KESTREL, by="test")
            pack = packs.current_pack(conn)
            ingest.ingest(conn, Path(first["data"]))
            pipeline.rebuild(conn, pack)
            conn.commit()
            flare = conn.execute("select fuzer_id, key from entity_key where kind = 'tag' and key like 'H1-%%' "
                                 "order by key limit 1").fetchone()

            with TestClient(api.app) as client:
                ok(client.put("/exceptions/types/flare_report", json={"by": "ana", "label": "Flare report",
                                                                      "role": "operator"}))
                ok(client.put("/exceptions/types/flare_note", json={"by": "ana", "label": "Flare note"}))  # no queue
                raise_ = lambda key, ref, t="flare_report": ok(client.post("/exceptions", json={
                    "type": t, "key": key, "title": f"Smoking flare ({key})", "by": "scada", "source": "scada",
                    "refs": [ref]}))

                first_x = raise_("F1", "tag:FLARE-TIP-9")
                assert first_x["unmatched"] == ["tag:FLARE-TIP-9"] and first_x["subjects"] == []
                [q] = [u for u in ok(client.get("/stewardship/unmatched")) if u["ref"] == "tag:FLARE-TIP-9"]
                assert q["exceptions"] == 1 and q["has_case"] and q["open"][0]["id"] == first_x["id"]

                bad = client.post(f"/exceptions/{first_x['id']}/resolve",
                                  json={"by": "ann", "ref": "tag:NOT-ON-IT", "fuzer_id": flare["fuzer_id"]})
                assert bad.status_code == 400
                fixed = ok(client.post(f"/exceptions/{first_x['id']}/resolve", json={
                    "by": "ann", "ref": "tag:FLARE-TIP-9", "fuzer_id": flare["fuzer_id"], "reason": "the HP flare tip"}))
                assert fixed["subjects"] == [flare["fuzer_id"]] and fixed["unmatched"] == []
                case = conn.execute("select subjects from work_item where id = %s", (fixed["item_id"],)).fetchone()
                assert flare["fuzer_id"] in case["subjects"]
                conn.rollback()
                assert not [u for u in ok(client.get("/stewardship/unmatched")) if u["ref"] == "tag:FLARE-TIP-9"]

                # the next one matches on its own, written either way
                assert raise_("F2", "tag:FLARE-TIP-9")["subjects"] == [flare["fuzer_id"]]
                assert raise_("F3", "flare-tip-9")["subjects"] == [flare["fuzer_id"]]

                # one no case works yet, resolved, joins the open case about that asset
                note = raise_("N1", "FLARE TIP NINE", t="flare_note")
                assert note["item_id"] is None and note["unmatched"] == ["FLARE TIP NINE"]
                joined = ok(client.post(f"/exceptions/{note['id']}/resolve", json={
                    "by": "ann", "ref": "FLARE TIP NINE", "fuzer_id": flare["fuzer_id"], "remember": False}))
                assert joined["joined_to"] and joined["item_id"]
                assert raise_("N2", "FLARE TIP NINE", t="flare_note")["unmatched"] == ["FLARE TIP NINE"]  # not kept

                # a rebuild's resolvers know the alias too
                assert rules.apply(conn, pack).aliases["flare-tip-9"] == ("tag", flare["key"])
                conn.rollback()

                # the decision log lists it, and reverses it
                [d] = [d for d in ok(client.get("/stewardship/decisions", params={"q": "flare"})) if d["kind"] == "alias"]
                assert d["by"] == "ann" and d["reason"] == "the HP flare tip" and d["what"] == f"FLARE-TIP-9 = tag:{flare['key']}"
                ok(client.post(f"/stewardship/aliases/{d['id']}/retire", json={"by": "ann", "reason": "wrong tip"}))
                assert raise_("F4", "tag:FLARE-TIP-9")["unmatched"] == ["tag:FLARE-TIP-9"]
                audit = conn.execute("select action, reason from source_audit where object = 'alias' order by id").fetchall()
                assert [a["action"] for a in audit] == ["create", "delete"] and audit[1]["reason"] == "wrong tip"
                conn.rollback()
    finally:
        packs.clear_cache()
        settings._cache.clear()
        settings._first.clear()
        with _admin() as a:
            a.execute(f'drop database if exists "{NAME}" with (force)')
