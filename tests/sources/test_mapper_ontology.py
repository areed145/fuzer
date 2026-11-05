"""Mappings checked against the ontology (#241): what a mapping makes, read from its YAML, and the warnings the
column mapper shows. Every built-in mapping makes only what the shipped ontology declares and allows."""

import pytest

from fuzer.core import db, ontology
from fuzer.sources import catalog, mapper

P6 = """source: p6
record_type: project_schedule
dataset: project_schedules
key: activity_id
entity:
  kind: project_schedule
  key: activity_id
links:
- rel: part_of
  when: project_id
  to: {kind: tag, from: project_id, as: tag}
- rel: refers_to
  when: location
  to: {from: location, as: floc}
- rel: refers_to
  when: work_order
  to: {ref: 'sap:work_order', from: work_order}
"""


@pytest.fixture(scope="module")
def conn():
    with db.connect() as c:
        db.reset(c)
        yield c


def test_produces_reads_entity_links_and_resolvers():
    made = mapper.produces(P6, lambda ref: "work_order" if ref == "sap:work_order" else None)
    assert made[0] == ("", "project_schedule", None)
    assert ("part_of", "project_schedule", "tag") in made
    assert {m[2] for m in made if m[0] == "refers_to"} == {"facility", "area", "tag", "work_order"}
    assert mapper.produces("not: [valid") == [] and mapper.produces("- a list") == []


def test_every_builtin_mapping_fits_the_ontology(conn):
    refs = {f"{raw['source']}:{raw['record_type']}": text for raw, text in catalog.seed_files()}

    def ref_kind(ref):
        made = mapper.produces(refs.get(ref, ""))
        return made[0][1] if made and not made[0][0] else None
    bad = {f"{raw['source']}/{raw['record_type']}": w for raw, text in catalog.seed_files()
           if (w := ontology.check_mapping(conn, mapper.produces(text, ref_kind)))}
    assert bad == {}


def test_a_new_dataset_gets_told_what_to_define(conn):
    w = ontology.check_mapping(conn, mapper.produces(P6, lambda ref: "work_order"))
    assert any("kind 'project_schedule' isn't in the ontology" in x for x in w)
    assert any("refers_to is a generic link" in x for x in w)
    fixed = """source: p6
record_type: project_schedule
key: activity_id
entity: {kind: activity, key: activity_id}
links:
- {rel: part_of, to: {kind: activity, from: parent_id}}
- {rel: located_at, to: {from: location, as: floc}}
- {rel: executes, to: {ref: 'sap:work_order', from: work_order}}
"""
    assert ontology.check_mapping(conn, mapper.produces(fixed, lambda ref: "work_order")) == []


def test_link_to_any_kind_by_key_round_trips():
    f = mapper.form(P6.replace("to: {kind: tag, from: project_id, as: tag}", "to: {kind: well, from: project_id}"))
    col = {c["name"]: c for c in f["columns"]}["project_id"]
    assert col["link"]["target"] == "kind:well" and col["link"]["rel"] == "part_of"
    col["link"]["target"] = "kind:activity"
    out = mapper.to_yaml(f, P6)
    assert "kind: activity" in out and "from: project_id" in out and "as: tag" not in out.split("location")[0]
