"""Source packs (#227): a source added from the UI alone is exported as one file, imported into a fresh database
of the same project, and gives the same result there: the same dataset settings, mapping, checks, records and links.
Importing again changes nothing; a pack whose mapping differs from one a person approved here leaves a draft."""

import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from test_add_source_ui_only import add_source, ok

from fakeplant import schedules
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.ops import pipeline
from fuzer.sources import catalog, ingest

pytestmark = pytest.mark.sequence


@pytest.fixture(scope="module")
def ui(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        yield okan_db.conn, client, okan_db.data


@pytest.fixture
def fresh(okan_db):
    """Another copy of the Okan project as built from its exports, without anything added since."""
    name = "okan_test_source_packs_fresh"
    admin = db.connect(autocommit=True, url=db.database_url())
    admin.execute(f'drop database if exists "{name}" with (force)')
    admin.execute(f'create database "{name}" template "tpl_okan"')
    try:
        with db.connect(url=make_conninfo(db.database_url(), dbname=name)) as c:
            yield c
    finally:
        admin.execute(f'drop database if exists "{name}" with (force)')
        admin.close()


def links(conn) -> dict:
    return {(r["rel"], r["dst_kind"]): r["n"] for r in conn.execute(
        """select e.rel, d.kind as dst_kind, count(*) as n from edge e join entity s on s.fuzer_id = e.src
           join entity d on d.fuzer_id = e.dst
           where s.kind = 'activity' and exists (select 1 from crosswalk x where x.fuzer_id = s.fuzer_id
                                                 and x.source = 'planning')
           group by 1, 2""")}


def records(conn) -> dict:
    return {r["source_key"]: r["payload"] for r in conn.execute(
        "select source_key, payload from source_record where source = 'planning'")}


def test_an_exported_source_imports_into_a_fresh_database(ui, fresh):
    conn, client, data = ui
    lake = Path(tempfile.mkdtemp(prefix="fzpack"))
    schedules.write(data, lake, fixed=True)
    _, out, _ = add_source(client, "planning", lake, "project_schedules",
                           {"load_mode": "snapshot", "owner": "ana", "schedule": "0 6 * * *"})
    assert out["load"]["loaded"] == ["project_schedules"]
    ok(client.post("/sources/datasets/project_schedules/checks", json={
        "kind": "range", "column": "percent_complete", "params": {"min": 0, "max": 100}, "by": "ana"}))
    pipeline.rebuild(conn, load_pack("okan"))
    conn.commit()

    pack = ok(client.get("/sources/systems/planning/pack"))["yaml"]
    raw = yaml.safe_load(pack)
    assert raw["fuzer_source_pack"] == 1 and raw["system"]["date_format"] == "%d/%m/%Y"
    (d,) = raw["datasets"]
    assert d["connection"] == "export" and d["schedule"] == "0 6 * * *" and d["owner"] == "ana"
    assert d["mappings"][0]["record_type"] == "project_schedule" and "kind: activity" in d["mappings"][0]["yaml"]
    assert d["checks"] == [{"kind": "range", "column": "percent_complete", "params": {"min": 0, "max": 100},
                            "blocking": False}]
    assert client.get("/sources/systems/nope/pack").status_code == 404

    # the same folder elsewhere in the other environment
    lake2 = Path(tempfile.mkdtemp(prefix="fzpack2"))
    schedules.write(data, lake2, fixed=True)
    catalog.ensure(fresh)
    rep = catalog.import_pack(fresh, pack, "ops", config={"export": {"path": str(lake2)}})
    fresh.commit()
    assert rep["system"] == ["system planning"] and rep["datasets"] == ["project_schedules"]
    assert rep["mappings"] == ["planning/project_schedule"] and rep["checks"] == ["project_schedules: range percent_complete"]
    assert catalog.export_pack(fresh, "planning") == pack.replace(str(lake), str(lake2))

    loaded = ingest.ingest(fresh, data, ["project_schedules"])
    assert loaded["loaded"] == ["project_schedules"]
    pipeline.rebuild(fresh, load_pack("okan"))
    fresh.commit()
    assert records(fresh) == records(conn)
    assert links(fresh) == links(conn) and links(fresh)[("executes", "work_order")] >= 3

    again = catalog.import_pack(fresh, pack, "ops", config={"export": {"path": str(lake2)}})
    assert set(again) == {"unchanged"}  # nothing to do the second time
    fresh.commit()


def test_a_pack_never_replaces_a_mapping_a_person_approved(ui):
    _, client, _ = ui
    pack = ok(client.get("/sources/systems/planning/pack"))["yaml"]
    changed = pack.replace("    - owner\n", "    - [responsible, owner]\n", 1)
    assert changed != pack
    rep = ok(client.post("/sources/packs", json={"yaml": changed, "by": "bob", "reason": "from the test site"}))
    assert rep["drafts"] == ["planning/project_schedule"] and "mappings" not in rep
    d = ok(client.get("/sources/datasets/project_schedules"))
    assert d["mappings"][0]["status"] == "draft" and d["mappings"][1]["current"]

    bad = client.post("/sources/packs", json={"yaml": "source: planning\nrecord_type: x\n", "by": "bob"})
    assert bad.status_code == 400 and "not a source pack" in bad.json()["detail"]
