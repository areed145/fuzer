"""Master data stewardship, gaps (#476): the Master data page shows fields most of a kind carry and some lack, values
written several ways, and open conflicts. A steward sets one spelling across the entities that use the others; it
holds through rebuilds, shows in the decision log, and one undo puts the values back."""

from pathlib import Path

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_gaps"


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _admin():
    return db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres"))


def _fresh_caches():
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def test_gaps_are_listed_and_a_spelling_is_fixed_across_entities_and_undone(monkeypatch):
    from fuzer import api
    from fuzer.ops import pipeline
    from fuzer.sources import ingest

    with _admin() as a:
        a.execute(f'drop database if exists "{NAME}" with (force)')
        a.execute(f'create database "{NAME}"')
    url = make_conninfo(db.database_url(), dbname=NAME)
    monkeypatch.setenv("FUZER_DATABASE_URL", url)
    monkeypatch.delenv("FUZER_PROJECT", raising=False)
    monkeypatch.setattr(db, "_default", db._default)
    _fresh_caches()
    value = lambda fid: conn.execute("select value #>> '{}' as v from golden where fuzer_id = %s and "
                                     "field = 'manufacturer'", (fid,)).fetchone()["v"]
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            first = bundles.init(conn, KESTREL, by="test")
            pack = packs.current_pack(conn)
            ingest.ingest(conn, Path(first["data"]))
            pipeline.rebuild(conn, pack)
            # one tag loses its description: most tags have one, so it is a gap
            kind, field = "tag", "description"
            gap = conn.execute("select g.fuzer_id from golden g join entity e using (fuzer_id) where e.kind = %s "
                               "and g.field = %s order by g.fuzer_id limit 1", (kind, field)).fetchone()["fuzer_id"]
            # one record writes a common manufacturer another way (capitals, "Inc."); planted in the source it won from
            usual = conn.execute("select value #>> '{}' as v from golden where field = 'manufacturer' and rule <> "
                                 "'derived' group by 1 having count(*) >= 2 order by count(*) desc, 1 limit 1"
                                 ).fetchone()["v"]
            odd_spelling = f"{usual.upper()} INC."
            planted = conn.execute(
                "select r.source, r.record_type, r.source_key, r.payload, g.fuzer_id from golden g join crosswalk c "
                "using (fuzer_id) join source_record r on (r.source, r.record_type, r.source_key) = (c.source, c.record_type, c.source_key) where g.field = "
                "'manufacturer' and g.value #>> '{}' = %s and g.rule <> 'derived' and c.source = g.source order by "
                "g.fuzer_id limit 1", (usual,)).fetchone()
            payload = {k: (odd_spelling if v == usual else v) for k, v in planted["payload"].items()}
            assert payload != planted["payload"]
            conn.execute("update source_record set payload = %s where (source, record_type, source_key) = (%s, %s, %s)",
                         (Jsonb(payload), planted["source"], planted["record_type"], planted["source_key"]))
            pipeline.rebuild(conn, pack)
            conn.execute("delete from golden where fuzer_id = %s and field = %s", (gap, field))
            conn.commit()

            with TestClient(api.app) as client:
                [c] = [g for g in ok(client.get("/stewardship/completeness")) if (g["kind"], g["field"]) == (kind, field)]
                assert c["missing"] >= 1 and gap in {x["fuzer_id"] for x in c["examples"]}

                # the two spellings are one manufacturer; the usual spelling is first
                [s] = [g for g in ok(client.get("/stewardship/consistency")) if g["field"] == "manufacturer"
                       and g["usual"] == usual]
                odd = [x for x in s["spellings"] if x["value"] != usual]
                assert [x["value"] for x in odd] == [odd_spelling]
                ids = odd[0]["fuzer_ids"]
                assert ids == [planted["fuzer_id"]]
                assert ok(client.get("/stewardship/conflicts"))  # sources disagree on a manufacturer somewhere

                fix = ok(client.post("/stewardship/fixes", json={"field": "manufacturer", "value": usual,
                                                                 "fuzer_ids": ids, "by": "sam", "reason": "one spelling"}))
                assert fix["changed"] == len(ids)
                assert {value(f) for f in ids} == {usual}
                conn.rollback()
                assert not [g for g in ok(client.get("/stewardship/consistency")) if g["usual"] == usual and g["field"] == "manufacturer"]
                again = client.post("/stewardship/fixes", json={"field": "manufacturer", "value": usual,
                                                                "fuzer_ids": ids, "by": "sam"})
                assert again.status_code == 400 and "already" in again.text

                pipeline.rebuild(conn, pack)  # a reviewer's choice: it holds on the rebuild
                conn.commit()
                assert {value(f) for f in ids} == {usual}
                conn.rollback()

                [d] = [d for d in ok(client.get("/stewardship/decisions")) if d["id"] == fix["batch"]]
                assert d["kind"] == "fix" and d["by"] == "sam" and d["reason"] == "one spelling"
                ok(client.post(f"/stewardship/fixes/{fix['batch']}/undo", json={"by": "sam"}))
                assert {value(f) for f in ids} == {odd_spelling}
                assert not conn.execute("select 1 from assertion where source = 'review' and fuzer_id = any(%s)",
                                        (ids,)).fetchone()
                conn.rollback()
                assert client.post(f"/stewardship/fixes/{fix['batch']}/undo", json={"by": "sam"}).status_code == 400
                log = {d["id"]: d for d in ok(client.get("/stewardship/decisions", params={"current": "false"}))}
                assert log[fix["batch"]]["reversed"] and log[fix["batch"]]["reversed_by"] == "sam"
    finally:
        _fresh_caches()
        with _admin() as a:
            a.execute(f'drop database if exists "{NAME}" with (force)')
