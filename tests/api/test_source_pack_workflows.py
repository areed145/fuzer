"""A source pack brings its workflows (#465): Kestrel's gas measurement system, imported from the Sources page as a
pack, brings its exception types and rules. Its loaded records raise exceptions into the right Inbox queues,
matched to the meters' tags and wells, and clear when the condition goes; its webhook takes work from the system
itself with the right secret only; and the pack exported again routes the same way in a fresh database."""

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
PACK = KESTREL / "packs" / "measurement.yaml"
BASE, FIRST, SECOND = "kestrel_wf_base", "kestrel_wf_one", "kestrel_wf_two"
SECRET = "kestrel-hook-secret"


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _admin():
    return db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres"))


def _fresh_caches():
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()


@pytest.fixture(scope="module")
def base():
    """Kestrel loaded and built once; each part of the test gets its own copy."""
    from fuzer.ops import pipeline
    from fuzer.sources import ingest

    with _admin() as a:
        for n in (BASE, FIRST, SECOND):
            a.execute(f'drop database if exists "{n}" with (force)')
        a.execute(f'create database "{BASE}"')
    url = make_conninfo(db.database_url(), dbname=BASE)
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATABASE_URL", url)
    mp.delenv("FUZER_PROJECT", raising=False)
    mp.setattr(db, "_default", db._default)
    _fresh_caches()
    try:
        with db.connect(url=url) as conn:
            db.init_schema(conn)
            first = bundles.init(conn, KESTREL, by="test")
            ingest.ingest(conn, Path(first["data"]))
            pipeline.rebuild(conn, packs.current_pack(conn))
            conn.commit()
        yield
    finally:
        mp.undo()
        _fresh_caches()
        with _admin() as a:
            for n in (BASE, FIRST, SECOND):
                a.execute(f'drop database if exists "{n}" with (force)')


def _copy(name: str, monkeypatch) -> str:
    with _admin() as a:
        a.execute(f'create database "{name}" template "{BASE}"')
    url = make_conninfo(db.database_url(), dbname=name)
    monkeypatch.setenv("FUZER_DATABASE_URL", url)
    _fresh_caches()
    return url


def _routing(conn) -> list[tuple]:
    """Each open exception the system brought: its type, key, the queue its case is in, and what it is about."""
    return [tuple(r.values()) for r in conn.execute(
        """
        select e.type, e.key, w.role, w.state, array(select k.key from entity_key k where k.fuzer_id = any(e.subjects)
                                                     and k.kind in ('tag', 'well') order by 1) as about
        from exception_record e left join work_item w on w.id = e.item_id
        where e.source = 'measurement' and e.state = 'open' order by e.type, e.key
        """)]


def test_a_pack_brings_its_exception_types_rules_and_webhook(base, monkeypatch, tmp_path):
    from fuzer import api

    url = _copy(FIRST, monkeypatch)
    monkeypatch.setenv("KESTREL_MEASUREMENT_HOOK", SECRET)
    folder = tmp_path / "measurement"
    shutil.copytree(KESTREL / "measurement", folder)
    text = PACK.read_text()

    with TestClient(api.app) as client, db.connect(url=url) as conn:
        # a folder that isn't here is refused before anything is written (#458)
        r = client.post("/sources/packs", json={"yaml": text, "config": {"export": {"path": str(tmp_path / "nowhere")}},
                                                "by": "ana"})
        assert r.status_code == 400 and "no folder" in r.text and "export" in r.text
        assert client.get("/sources/systems/measurement/workflows").status_code == 404

        report = ok(client.post("/sources/packs", json={"yaml": text, "config": {"export": {"path": str(folder)}},
                                                        "by": "ana"}))
        assert report["exception_types"] == ["short_gas_day", "estimated_volume"]
        assert report["exception_rules"] == ["short_day", "estimated"]
        again = ok(client.post("/sources/packs", json={"yaml": text, "config": {"export": {"path": str(folder)}},
                                                       "by": "ana"}))
        assert "exception_types" not in again and "exception_rules" not in again  # safe to repeat

        load = ok(client.post("/sources/datasets/daily_volumes/run"))
        assert load["loaded"] == ["daily_volumes"]
        assert load["exceptions"]["measurement/short_day"]["raised"] == 2
        assert load["exceptions"]["measurement/estimated"]["raised"] == 2
        routed = _routing(conn)
        short = [r for r in routed if r[0] == "short_gas_day"]
        assert [(r[2], r[3], r[4]) for r in short] == [("production_engineer", "open", ["C1-FT-3005"]),
                                                      ("production_engineer", "open", ["H1-FT-8010"])]
        assert {r[2] for r in routed if r[0] == "estimated_volume"} == {"engineer"}
        assert sorted(k for r in routed if r[0] == "estimated_volume" for k in r[4]) == ["KR 12-03H", "KR 12-04H"]

        work = ok(client.get("/sources/systems/measurement/workflows"))
        assert {t["name"]: t["open"] for t in work["types"]} == {"estimated_volume": 2, "short_gas_day": 2}
        assert work["webhook"] == {"path": "/hooks/measurement", "secret": "env:KESTREL_MEASUREMENT_HOOK", "ready": True}
        assert ok(client.get("/sources/systems/measurement/pack"))["yaml"].count("exception_") == 2

        # the condition goes: the trip's day reads a full 24 h on the sales meter and 23.2 h (between the rule's
        # raise and clear levels, so still open) on C1
        csv = folder / "daily_volumes.csv"
        lines = csv.read_text().splitlines()
        csv.write_text("\n".join(
            line.replace(",22.2,", ",24.0,") if line.startswith("MR-801,") and ",today-1d," in line
            else line.replace(",22.2,", ",23.2,") if line.startswith("MR-301,") and ",today-1d," in line
            else line for line in lines) + "\n")
        load = ok(client.post("/sources/datasets/daily_volumes/run"))
        assert load["exceptions"]["measurement/short_day"] == {"raised": 0, "refreshed": 0, "cleared": 1}
        after = _routing(conn)
        assert [r[4] for r in after if r[0] == "short_gas_day"] == [["C1-FT-3005"]]
        closed = conn.execute(
            """
            select e.state, w.state as case_state from exception_record e join work_item w on w.id = e.item_id
            where e.type = 'short_gas_day' and e.key like 'MR-801:%%'
            """).fetchone()
        assert closed["state"] == "cleared" and closed["case_state"] in ("done", "cancelled")

        # the webhook: the right secret raises, a wrong or missing one is refused, other types are not its to send
        call = {"type": "short_gas_day", "key": "MR-101:manual", "title": "Meter MR-101 stopped",
                "refs": ["tag:A1-FT-1002"]}
        assert client.post("/hooks/measurement", json=call).status_code == 401
        assert client.post("/hooks/measurement", json=call, headers={"X-Fuzer-Secret": "guess"}).status_code == 401
        assert client.post("/hooks/nowhere", json=call, headers={"X-Fuzer-Secret": SECRET}).status_code == 401
        r = client.post("/hooks/measurement", json={**call, "type": "source_issue"}, headers={"X-Fuzer-Secret": SECRET})
        assert r.status_code == 400 and "only the exception types it brought" in r.text
        raised = ok(client.post("/hooks/measurement", json=call, headers={"X-Fuzer-Secret": SECRET}))
        assert raised["raised"] and raised["source"] == "measurement" and not raised["unmatched"]
        assert ("short_gas_day", "MR-101:manual", "production_engineer", "open", ["A1-FT-1002"]) in _routing(conn)
        cleared = ok(client.post("/hooks/measurement", json={"type": "short_gas_day", "key": "MR-101:manual",
                                                              "clear": True, "reason": "back on"},
                                 headers={"X-Fuzer-Secret": SECRET}))
        assert cleared["state"] == "cleared"
        monkeypatch.delenv("KESTREL_MEASUREMENT_HOOK")
        assert client.post("/hooks/measurement", json=call, headers={"X-Fuzer-Secret": SECRET}).status_code == 401

        exported = ok(client.get("/sources/systems/measurement/pack"))["yaml"]
        conn.rollback()

    # the exported pack, in a fresh database, routes the same way
    url = _copy(SECOND, monkeypatch)
    out = yaml.safe_load(exported)
    out["connections"][0]["config"]["path"] = str(KESTREL / "measurement")
    with TestClient(api.app) as client, db.connect(url=url) as conn:
        assert ok(client.post("/sources/packs", json={"yaml": yaml.safe_dump(out), "by": "ana"}))["exception_rules"]
        ok(client.post("/sources/datasets/daily_volumes/run"))
        assert _routing(conn) == routed
