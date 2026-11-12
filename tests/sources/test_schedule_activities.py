"""Project schedules promoted into the ontology (#241): the P6 table loaded from the Sources API with a mapping
that uses the shipped `activity` kind and specific link types, so activities join the graph: part of their
parent activity, located at the tag, area, facility or well they're planned on, executing their SAP work
order. The mapping passes the ontology check and nothing it makes needs a definition."""

import csv
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fakeplant import schedules
from fuzer.core.pack import load_pack
from fuzer.graph import ontology as model
from fuzer.ops import pipeline
from fuzer.sources import ingest

ACTIVITIES = """# Project schedules (Primavera P6): projects and their activities, one row each.
source: p6
record_type: project_schedule
shape: hierarchy
dataset: project_schedules
key: activity_id
entity:
  kind: activity
  key: activity_id
  fields:
    - [description, name]
    - wbs
    - start
    - finish
    - baseline_start
    - baseline_finish
    - actual_start
    - actual_finish
    - [percent_complete, {from: percent_complete, as: number}]
    - owner
    - status
links:
  - {rel: part_of, when: parent_id, to: {kind: activity, from: parent_id}}
  - {rel: located_at, when: location, to: {from: location, as: floc}}
  - {rel: executes, when: work_order, to: {ref: 'sap:work_order', from: work_order}}
"""


@pytest.fixture(scope="module")
def plant(okan_db):
    data = Path(tempfile.mkdtemp(prefix="fzsched"))
    shutil.copytree(okan_db.data, data, dirs_exist_ok=True)
    rows, _ = schedules.rows(okan_db.data)
    rows = list({r["activity_id"]: r for r in rows}.values())  # its planted duplicate key would hold the load (#227)
    with (data / "project_schedules.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATA_DIR", str(data))
    from fuzer import api

    with TestClient(api.app) as client:
        yield okan_db.conn, client
    mp.undo()


def test_activities_join_the_graph(plant):
    conn, client = plant
    form = client.post("/sources/mapper/form", json={"yaml": ACTIVITIES}).json()
    assert form["warnings"] == []
    r = client.post("/sources/project_schedules/load", json={"yaml": ACTIVITIES, "by": "ana"})
    assert r.status_code == 200, r.text
    assert r.json()["loaded"] == ["project_schedules"]
    pipeline.rebuild(conn, load_pack("okan"))

    kinds = {r["kind"]: r["n"] for r in conn.execute(
        "select kind, count(*) as n from entity where retired_at is null and merged_into is null group by kind")}
    assert kinds.get("activity", 0) >= 20 and "project_schedule" not in kinds
    links = {(r["rel"], r["dst_kind"]): r["n"] for r in conn.execute(
        """select e.rel, d.kind as dst_kind, count(*) as n from edge e join entity s on s.fuzer_id = e.src
           join entity d on d.fuzer_id = e.dst where s.kind = 'activity' group by 1, 2""")}
    assert links[("part_of", "activity")] >= 15  # the hierarchy: activities under their project
    assert links[("executes", "work_order")] >= 3  # turnaround activities and their SAP orders
    assert links.get(("located_at", "tag"), 0) and links.get(("located_at", "facility"), 0)
    assert not any(rel == "refers_to" for rel, _ in links)

    m = model.model(conn)
    assert m["needs_definition"] == {"kinds": [], "relations": [], "not_allowed": []}
    act = next(k for k in m["kinds"] if k["kind"] == "activity")
    assert act["declared"] and act["group"] == "Planning"


def test_the_turnaround_s_curve_from_the_loaded_schedule(plant):
    """P5.9b (#360): the 2026 turnaround's progress against its baseline, loaded here (tests run in any order); the
    planted 112% row is named and left out of the curve, not plotted."""
    conn, client = plant
    r = client.post("/sources/project_schedules/load", json={"yaml": ACTIVITIES, "by": "ana"})  # tests run in any order
    assert r.status_code == 200, r.text
    # the export writes dd/mm/yyyy: the planner sets the system's date format, as the Sources page offers
    conn.execute("""update source_system s set date_format = '%d/%m/%Y' from source_dataset d
                    where d.system_id = s.id and d.name = 'project_schedules'""")
    ingest.ingest(conn, os.environ["FUZER_DATA_DIR"], exports=["project_schedules"], full=True)  # read again, same file
    pipeline.rebuild(conn, load_pack("okan"))
    conn.execute("update source_dataset set fingerprint = null where name = 'project_schedules'")  # a later load reads it
    conn.commit()
    fig = client.get("/analytics/figures/turnaround-progress", params={"revision": "OK-TAR-2026"})
    assert fig.status_code == 200, fig.text
    fig = fig.json()
    s = fig["summary"]
    assert [d["name"] for d in fig["spec"]["data"]] == ["Planned (baseline)", "Forecast (today's dates)", "Earned"]
    assert "Not counted: OK-TAR-2026-A0010, a percent complete of 112, outside 0 to 100." in s["warnings"]
    # as of the export (30 December), the scope freeze is the only work under way, and its row can't be counted
    assert s["headline"] == "No work is planned done by 30 Dec 2025; the work is finishing on its baseline."
    assert [x["label"] for x in s["stats"]][:2] == ["Earned", "Planned by now"]
    days = fig["spec"]["data"][0]["x"]
    assert days == sorted(days) and fig["spec"]["data"][0]["y"][-1] == 100
