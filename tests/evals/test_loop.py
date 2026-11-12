"""The loop acceptance eval (P1.7): every type of work on a bundle goes arrive to counted, and a type that can't
says at which step and why."""

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fuzer.evals import loop

# Each field type's job as the loop eval gave it to the phone, which the browser test renders at 390 px.
# FUZER_WRITE_FIXTURES=1 rewrites it from an Okan run.
JOBS = Path(__file__).resolve().parents[2] / "ui" / "tests" / "fixtures" / "loop-field-jobs.json"


def _json(x):
    return x.isoformat() if isinstance(x, (date, datetime)) else float(x) if isinstance(x, Decimal) else str(x)


def _shape(job: dict) -> dict:
    return {"keys": sorted(job), "finding": sorted(job["finding"]), "item": sorted({k for i in job["finding"]["items"]
                                                                                   for k in i}),
            "asset": sorted({k for a in job["assets"] for k in a})}


def test_every_type_of_work_on_okan_completes_the_loop(okan_db):
    res = loop.run(okan_db.conn)
    done = {r["type"]: r for r in res["types"]}
    assert {"upset", "iow_breach", "sap_notification", "machine_trip", "machine_health", "records_gap",
            "source_issue"} <= set(done), sorted(done)  # every routed type, declared before any was raised
    assert not res["failed"], "\n".join(res["failed"])
    assert all(list(r["steps"]) == list(loop.STEPS) for r in res["types"])
    assert done["upset"]["steps"]["route"] == "started upset" and done["upset"]["steps"]["count"].endswith("(Resolved)")
    assert done["sap_notification"]["steps"]["triage"] == "on the board, to dispatch"
    assert done["source_issue"]["steps"]["field"].startswith("skipped")  # data work stays at the desk

    jobs = {r["type"]: r["job"] for r in res["types"] if "job" in r}
    if os.environ.get("FUZER_WRITE_FIXTURES"):
        JOBS.write_text(json.dumps(jobs, indent=1, sort_keys=True, default=_json) + "\n")
    kept = json.loads(JOBS.read_text())
    assert sorted(kept) == sorted(jobs), "ui/tests/fixtures/loop-field-jobs.json is stale: FUZER_WRITE_FIXTURES=1"
    assert {t: _shape(j) for t, j in kept.items()} == {t: _shape(j) for t, j in jobs.items()}, \
        "the field job changed shape: rewrite the fixture with FUZER_WRITE_FIXTURES=1 and check the 390 px test"


def test_a_type_that_cannot_complete_says_at_which_step(okan_db, monkeypatch):
    monkeypatch.setitem(loop.ARRIVE, "sap_notification", {"equipment": "fz-pump-raw-id"})  # an id the field can't read
    [r] = loop.run(okan_db.conn, only=["sap_notification"])["types"]
    assert r["failed"] == "field" and "raw ids" in r["why"] and list(r["steps"]) == list(loop.STEPS[:5])


def test_every_type_of_work_on_kestrel_completes_the_loop(monkeypatch):
    """The same loop on the second bundle, stood up from its seed bundle as a deployment would be."""
    from pathlib import Path

    from psycopg.conninfo import make_conninfo

    from fuzer.core import bundles, db, settings
    from fuzer.core import pack as packs
    from fuzer.ops import pipeline
    from fuzer.sources import ingest

    name = "kestrel_loop"
    admin = lambda: db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres"))
    with admin() as a:
        a.execute(f'drop database if exists "{name}" with (force)')
        a.execute(f'create database "{name}"')
    url = make_conninfo(db.database_url(), dbname=name)
    monkeypatch.setenv("FUZER_DATABASE_URL", url)
    monkeypatch.delenv("FUZER_PROJECT", raising=False)
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            first = bundles.init(conn, Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel",
                                 by="test")
            ingest.ingest(conn, Path(first["data"]))
            pipeline.rebuild(conn, packs.current_pack(conn))
            conn.commit()
            res = loop.run(conn)
            assert len(res["types"]) >= 7 and not res["failed"], "\n".join(res["failed"])
    finally:
        packs.clear_cache()
        settings._cache.clear()
        settings._first.clear()
        with admin() as a:
            a.execute(f'drop database if exists "{name}" with (force)')
