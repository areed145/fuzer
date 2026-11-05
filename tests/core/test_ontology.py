"""The data model as data (#241): the shipped ontology seeded once and kept under a person's edits, adding and
editing kinds and link types with an audit trail, declared next to observed, renaming a custom kind across
the register and its mappings, and the warnings a mapping gets."""

import pytest

from fuzer.core import ontology
from fuzer.core.ontology import OntologyError
from fuzer.graph import ontology as model
from fuzer.sources import catalog

GIZMO = """# test gizmos
source: test
record_type: gizmo
shape: records
key: id
entity:
  kind: gizmo
  fields: [description]
links:
  - {rel: part_of, to: {kind: area, from: area}}
mentions:
  - {from: description, kinds: [tag, gizmo]}
"""


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


def test_builtin_covers_what_the_register_holds(conn):
    m = model.model(conn)
    assert m["needs_definition"]["kinds"] == [] and m["needs_definition"]["relations"] == []
    assert m["needs_definition"]["not_allowed"] == []  # every observed link fits its link type
    tag = next(k for k in m["kinds"] if k["kind"] == "tag")
    assert tag["declared"] and tag["status"] == "core" and tag["colour"].startswith("#") and tag["count"] > 100
    assert {v["id"] for v in m["views"]} >= {"physical", "flow", "planning"}
    part_of = next(r for r in m["model"]["relations"] if r["name"] == "part_of")
    assert part_of["count"] > 0 and "tag" in part_of["from_kinds"]


def test_seed_is_idempotent_and_keeps_edits(conn):
    assert ontology.seed(conn) == {}
    ontology.update_kind(conn, "tag", "ana", "plant wording", description="A functional location.")
    conn.execute("update ontology_relation set description = 'stale' where name = 'installed_at'")  # file moved on
    conn.execute("delete from ontology_kind where name = 'well_test'")
    conn.commit()
    report = ontology.seed(conn)
    assert report == {"kinds_added": ["well_test"], "relations_updated": ["installed_at"]}
    assert ontology.kind(conn, "tag")["description"] == "A functional location."  # edited: kept
    assert ontology.relation(conn, "installed_at")["description"].startswith("Equipment installed")
    assert [h["action"] for h in ontology.history(conn, "kind", "tag")] == ["update"]


def test_add_edit_and_delete(conn):
    with pytest.raises(OntologyError, match="isn't a name"):
        ontology.add_kind(conn, "Bad Name", "ana")
    k = ontology.add_kind(conn, "project", "ana", "P6 schedules", label="Project", group="Planning",
                          description="A capital or turnaround project.")
    assert k["status"] == "custom" and k["updated_by"] == "ana"
    with pytest.raises(OntologyError, match="already exists"):
        ontology.add_kind(conn, "project", "ana")
    with pytest.raises(OntologyError, match="unknown kind"):
        ontology.add_relation(conn, "closes_out", "ana", from_kinds=["milestone"], to_kinds=["work_order"])
    ontology.add_kind(conn, "milestone", "ana", status="draft")
    r = ontology.add_relation(conn, "closes_out", "ana", view="planning", from_kinds=["milestone"], to_kinds=["work_order"])
    assert r["from_kinds"] == ["milestone"] and r["status"] == "custom"
    assert ontology.update_kind(conn, "milestone", "ana", status="custom", label="Milestone")["edited"]
    with pytest.raises(OntologyError, match="stays core"):
        ontology.update_kind(conn, "tag", "ana", status="custom")
    with pytest.raises(OntologyError, match="can't be deleted"):
        ontology.delete_kind(conn, "tag", "ana")
    ontology.delete_kind(conn, "milestone", "ana", "not needed")
    assert ontology.relation(conn, "closes_out")["from_kinds"] == []  # no longer allowed anywhere
    ontology.delete_relation(conn, "closes_out", "ana")
    assert [h["action"] for h in ontology.history(conn, "kind", "milestone")] == ["delete", "update", "create"]


def test_undeclared_kinds_and_links_need_a_definition(conn):
    conn.execute("insert into entity (fuzer_id, kind) values ('fz-test-sched-1', 'project_schedule')")
    tag = conn.execute("select fuzer_id from entity where kind = 'tag' limit 1").fetchone()["fuzer_id"]
    wo = conn.execute("select fuzer_id from entity where kind = 'work_order' limit 1").fetchone()["fuzer_id"]
    conn.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-test-sched-1', 'refers_to', %s, '{p6}', 1), "
                 "('fz-test-sched-1', 'schedules', %s, '{p6}', 1), (%s, 'installed_at', %s, '{test}', 1)", (wo, tag, wo, tag))
    conn.commit()
    model._refresh_summary(conn)
    m = model.model(conn)
    assert m["needs_definition"]["kinds"] == ["project_schedule"]
    assert m["needs_definition"]["relations"] == ["schedules"]
    assert {"src_kind": "work_order", "rel": "installed_at", "dst_kind": "tag", "n": 1} in m["needs_definition"]["not_allowed"]
    sched = next(k for k in m["kinds"] if k["kind"] == "project_schedule")
    assert not sched["declared"] and "label" not in sched
    conn.execute("delete from edge where src in ('fz-test-sched-1', %s)", (wo,))
    conn.execute("delete from entity where fuzer_id = 'fz-test-sched-1'")
    conn.commit()
    model._refresh_summary(conn)


def test_rewrite_mapping_keeps_layout():
    out = model.rewrite_mapping(GIZMO, "gizmo", "widget")
    assert "kind: widget\n" in out and "kinds: [tag, widget]" in out and "# test gizmos" in out
    assert "record_type: gizmo" in out  # only kinds change
    assert model.rewrite_mapping("to: {kind: 'gizmo', from: x}", "gizmo", "w") == "to: {kind: 'w', from: x}"
    assert model.rewrite_mapping("kind: gizmo_set", "gizmo", "w") == "kind: gizmo_set"


def test_rename_a_custom_kind_everywhere(conn):
    ontology.add_kind(conn, "gizmo", "ana", status="draft")
    ontology.add_relation(conn, "fits", "ana", from_kinds=["gizmo"], to_kinds=["tag"])
    conn.execute("insert into entity (fuzer_id, kind) values ('fz-test-gizmo-1', 'gizmo')")
    conn.execute("insert into entity_key (kind, key, fuzer_id) values ('gizmo', 'G1', 'fz-test-gizmo-1')")
    conn.execute("insert into mapping_version (source, record_type, yaml, status, author, approved_by, approved_at) "
                 "values ('test', 'gizmo', %s, 'approved', 'ana', 'ana', now())", (GIZMO,))
    conn.commit()
    with pytest.raises(OntologyError, match="renamed in a release"):
        model.rename_kind(conn, "tag", "position", "ana")
    with pytest.raises(OntologyError, match="already exists"):
        model.rename_kind(conn, "gizmo", "tag", "ana")
    out = model.rename_kind(conn, "gizmo", "widget", "ana", "plant word")
    assert out["entities"] == 1 and out["mappings"][0].startswith("test/gizmo v")
    assert conn.execute("select kind from entity where fuzer_id = 'fz-test-gizmo-1'").fetchone()["kind"] == "widget"
    assert conn.execute("select kind from entity_key where fuzer_id = 'fz-test-gizmo-1'").fetchone()["kind"] == "widget"
    assert ontology.kind(conn, "gizmo") is None and ontology.relation(conn, "fits")["from_kinds"] == ["widget"]
    current = next(m for m in catalog.current_mappings(conn) if m["record_type"] == "gizmo")
    assert "kind: widget" in current["yaml"] and "renamed" in current["note"]
    assert ontology.history(conn, "kind", "widget")[0]["action"] == "rename"


def test_check_mapping_warnings(conn):
    assert ontology.check_mapping(conn, [("", "tag", None), ("part_of", "tag", "area")]) == []
    w = ontology.check_mapping(conn, [("", "project_schedule", None), ("part_of", "project_schedule", "tag"),
                                      ("refers_to", "project_schedule", "work_order"), ("schedules", "tag", "tag")])
    assert any("kind 'project_schedule' isn't in the ontology" in x for x in w)
    assert any("part_of links from" in x and "not project_schedule" in x for x in w)
    assert any("refers_to is a generic link" in x for x in w)
    assert any("link type 'schedules' isn't in the ontology" in x for x in w)
    assert len(w) == len(set(w))


def test_api_edits_with_who_and_why(okan_db, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("FUZER_DATABASE_URL", okan_db.url)
    from fuzer import api

    with TestClient(api.app) as c:
        o = c.get("/ontology").json()
        assert o["needs_definition"]["kinds"] == [] and any(k["kind"] == "tag" for k in o["kinds"])
        r = c.post("/ontology/kinds", json={"name": "turbine_skid", "label": "Turbine skid", "by": "ana", "reason": "new"})
        assert r.status_code == 200 and r.json()["status"] == "custom"
        assert c.post("/ontology/kinds", json={"name": "turbine_skid", "by": "ana"}).status_code == 400
        r = c.put("/ontology/kinds/turbine_skid", json={"name": "skid", "description": "A packaged skid.", "by": "ana"})
        assert r.json()["name"] == "skid" and r.json()["description"] == "A packaged skid."
        r = c.post("/ontology/relations", json={"name": "mounted_on", "from_kinds": ["skid"], "to_kinds": ["area"], "by": "ana"})
        assert r.status_code == 200
        assert c.put("/ontology/relations/mounted_on", json={"dated": True, "by": "ana"}).json()["dated"] is True
        assert {k["name"] for k in c.get("/ontology/declared").json()["kinds"]} >= {"skid", "tag"}
        assert [h["action"] for h in c.get("/ontology/history", params={"name": "skid"}).json()][:2] == ["update", "rename"]
        assert c.delete("/ontology/relations/mounted_on", params={"by": "ana"}).status_code == 200
        assert c.delete("/ontology/kinds/skid", params={"by": "ana"}).status_code == 200
        assert c.delete("/ontology/kinds/tag", params={"by": "ana"}).status_code == 400
