"""Claude's master data proposals (#476): planted problems (tags written other systems' ways, a ref naming nothing,
a misspelt manufacturer) get proposals with a confidence, reason and evidence; the eval scores them; accepting one
applies it the way a steward would (an alias that also resolves the exception, a spelling fix); a rejected one
stays rejected; answers are cached; bulk accept is refused until its threshold is set."""

from pathlib import Path

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
NAME = "kestrel_proposals"


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _admin():
    return db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres"))


def _fresh_caches():
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def test_claude_proposes_fixes_that_a_steward_accepts_and_the_eval_scores(monkeypatch):
    from fuzer import api
    from fuzer.evals import steward_eval
    from fuzer.graph import proposals
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
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            first = bundles.init(conn, KESTREL, by="test")
            pack = packs.current_pack(conn)
            ingest.ingest(conn, Path(first["data"]))
            pipeline.rebuild(conn, pack)
            conn.commit()

            res = steward_eval.run(conn, scripted_wrong=0)
            key = res.pop("key")
            assert res["planted"] == res["asked"] >= 5, (res["misses"], key)
            assert res["precision"] == 1.0 and res["recall"] == 1.0 and res["declined_correctly"] == 1, res
            assert res["by_threshold"]["0.9"]["precision"] == 1.0

            # cached: asking again without a change asks nothing
            again = proposals.propose(conn, client=steward_eval.scripted(key), kinds=("unresolved", "spelling"))
            assert again["asked"] == 0 and again["cached"] >= 5

            with TestClient(api.app) as client:
                listed = ok(client.get("/stewardship/proposals"))
                assert listed["bulk_accept_at"] == 0
                by_subject = {f"{p['kind']}|{p['subject']}": p for p in listed["proposals"]}
                assert all(by_subject[k]["answer"]["reason"] for k in key)

                # an unresolved ref: accepting records the alias and resolves the open exception that carried it
                k = next(k for k, v in key.items() if k.startswith("unresolved|") and v["fuzer_id"])
                p = by_subject[k]
                assert p["pick"].startswith(key[k]["option"])
                out = ok(client.post(f"/stewardship/proposals/{p['id']}/accept", json={"by": "sam"}))
                assert out["alias"] and out["fuzer_id"] == key[k]["fuzer_id"]
                ref = k.split("|", 1)[1]
                ex = conn.execute("select subjects, unmatched from exception_record where type = 'steward_eval' "
                                  "and title like '%%' || %s", (ref,)).fetchone()
                assert key[k]["fuzer_id"] in ex["subjects"] and ref not in (ex["unmatched"] or [])
                conn.rollback()
                assert client.post(f"/stewardship/proposals/{p['id']}/accept", json={"by": "sam"}).status_code == 400

                # the decoy: Claude said none, so there is nothing to accept; the steward rejects it
                d = by_subject[next(k for k, v in key.items() if k.startswith("unresolved|") and v["fuzer_id"] is None)]
                assert d["answer"]["choice"] == "none"
                assert client.post(f"/stewardship/proposals/{d['id']}/accept", json={"by": "sam"}).status_code == 400
                ok(client.post(f"/stewardship/proposals/{d['id']}/reject", json={"by": "sam", "reason": "a test ref"}))

                # the misspelt manufacturer: accepting sets the usual spelling (a fix, in the decision log)
                s = next(v for k, v in by_subject.items() if k.startswith("spelling|manufacturer|"))
                fixed = ok(client.post(f"/stewardship/proposals/{s['id']}/accept", json={"by": "sam"}))
                usual = key[f"spelling|{s['subject']}"]["keep"]
                assert fixed["fix"] and conn.execute(
                    "select count(*) as n from golden where field = 'manufacturer' and value #>> '{}' = %s",
                    (usual[:-2] + usual[-1],)).fetchone()["n"] == 0
                conn.rollback()
                assert fixed["fix"] in {x["id"] for x in ok(client.get("/stewardship/decisions"))}

                # bulk accept: refused at 0, then accepts what is left at or above the threshold
                assert client.post("/stewardship/proposals/accept-confident", json={"by": "sam"}).status_code == 400
                settings.set(conn, "stewardship.bulk_accept_at", 0.9, by="test")
                bulk = ok(client.post("/stewardship/proposals/accept-confident", json={"by": "sam"}))
                assert bulk["accepted"] == sum(1 for k, v in key.items() if k.startswith("unresolved|") and v["fuzer_id"]) - 1
                left = ok(client.get("/stewardship/proposals"))["proposals"]
                assert not [x for x in left if x["subject"] in {k.split("|", 1)[1] for k in key}]
                rejected = ok(client.get("/stewardship/proposals", params={"status": "rejected"}))["proposals"]
                assert [x["subject"] for x in rejected] == [d["subject"]]

            # a wrong answer shows in the score
            with db.connect(url=url) as c2:
                worse = steward_eval.score(c2, key)
            assert worse["planted"] == len(key)
    finally:
        _fresh_caches()
        with _admin() as a:
            a.execute(f'drop database if exists "{NAME}" with (force)')
