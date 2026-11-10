"""Master data stewardship, merge and split (#476): from the entity page a steward says two entities are the same
thing, or a record is not this thing. Each shows a preview first, holds across rebuilds, lands in the decision log as
one decision, and one undo reverses it."""

from pathlib import Path

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_merge_split"


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _admin():
    return db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres"))


def _fresh_caches():
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def test_a_steward_merges_and_splits_entities_with_a_preview_and_an_undo(monkeypatch):
    from fuzer import api
    from fuzer.api import rebuilder
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

    def rebuilt():
        rebuilder.wait()
        assert "error" not in rebuilder.last, rebuilder.last
        conn.rollback()

    owner = lambda ref: conn.execute(
        "select fuzer_id from crosswalk where source || ':' || record_type || ':' || source_key = %s",
        (ref,)).fetchone()["fuzer_id"]
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            first = bundles.init(conn, KESTREL, by="test")
            pack = packs.current_pack(conn)
            ingest.ingest(conn, Path(first["data"]))
            pipeline.rebuild(conn, pack)
            conn.commit()
            # an entity several records agree on, and two tags that are different things
            many = conn.execute(
                "select c.fuzer_id, array_agg(c.source || ':' || c.record_type || ':' || c.source_key order by "
                "c.source, c.record_type, c.source_key) as refs from crosswalk c join entity e using (fuzer_id) "
                "where e.kind = 'tag' and e.merged_into is null group by c.fuzer_id having count(*) >= 3 "
                "order by c.fuzer_id limit 1").fetchone()
            a_id, b_id = [r["fuzer_id"] for r in conn.execute(
                "select e.fuzer_id from entity e join crosswalk c using (fuzer_id) where e.kind = 'tag' "
                "and e.merged_into is null and e.fuzer_id <> %s group by e.fuzer_id order by e.fuzer_id limit 2",
                (many["fuzer_id"],))]
            conn.rollback()

            with TestClient(api.app) as client:
                # split: the preview says what stays and what the record brings; nothing changes yet
                odd = many["refs"][0]
                p = ok(client.get("/stewardship/split/preview", params={"fuzer_id": many["fuzer_id"], "ref": odd}))
                assert p["ref"] == odd and odd not in p["stays"] and len(p["stays"]) == len(many["refs"]) - 1
                assert owner(odd) == many["fuzer_id"]
                conn.rollback()
                s = ok(client.post("/stewardship/split", json={"fuzer_id": many["fuzer_id"], "ref": odd, "by": "sam",
                                                               "reason": "wrong unit"}))
                rebuilt()
                assert owner(odd) != many["fuzer_id"]
                assert {owner(r) for r in p["stays"]} == {many["fuzer_id"]}  # the rest stay together
                conn.rollback()
                # it holds on the next rebuild too
                pipeline.rebuild(conn, pack)
                assert owner(odd) != many["fuzer_id"]
                conn.rollback()

                # merge: a different kind or the same entity is refused; the preview lists keys and disagreements
                assert client.get("/stewardship/merge/preview", params={"a": a_id, "b": a_id}).status_code == 400
                m = ok(client.get("/stewardship/merge/preview", params={"a": a_id, "b": b_id}))
                assert m["a"]["fuzer_id"] == a_id and m["records"] >= 2 and len(m["keys"]) >= 2
                done = ok(client.post("/stewardship/merge", json={"a": a_id, "b": b_id, "by": "sam"}))
                rebuilt()
                ra, rb = m["a"]["records"][0]["ref"], m["b"]["records"][0]["ref"]
                assert owner(ra) == owner(rb)
                gone = conn.execute("select merged_into from entity where fuzer_id = any(%s) and merged_into is not "
                                    "null", ([a_id, b_id],)).fetchall()
                assert len(gone) == 1 and gone[0]["merged_into"] in (a_id, b_id)
                conn.rollback()

                # the decision log shows each as one decision; undo reverses it
                log = {d["id"]: d for d in ok(client.get("/stewardship/decisions"))}
                assert log[s["batch"]]["kind"] == "split" and log[s["batch"]]["reason"] == "wrong unit"
                assert log[done["batch"]]["kind"] == "merge" and log[done["batch"]]["by"] == "sam"
                ok(client.post("/stewardship/pins/undo", json={"batch": done["batch"], "by": "sam"}))
                rebuilt()
                assert owner(ra) != owner(rb)
                ok(client.post("/stewardship/pins/undo", json={"batch": s["batch"], "by": "sam"}))
                rebuilt()
                assert len({owner(r) for r in many["refs"]}) == 1
                again = client.post("/stewardship/pins/undo", json={"batch": s["batch"], "by": "sam"})
                assert again.status_code == 400
                audit = [r["action"] for r in conn.execute(
                    "select action from source_audit where object = 'pin' order by id")]
                assert audit == ["create", "create", "delete", "delete"]
    finally:
        _fresh_caches()
        with _admin() as a:
            a.execute(f'drop database if exists "{NAME}" with (force)')
