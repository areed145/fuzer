"""A new source added from the UI alone (#227): only the calls the Sources page's Add a source makes, with the
suggested mapping accepted as offered. No mapping YAML is written by hand, and nothing in the repository changes
but the fake plant's export and this test.

- A planning system's project schedules (a Delta table Fuzer has never seen): the date-format hint, the suggested
  mapping (activities part of their parent, located at their plant location in whatever style it's written,
  executing their SAP work order), a first load held on the duplicate key, the planner's fix, a load, the graph,
  then an update that moves progress and drops a cancelled activity.
- A historian's point list: suggested as time series owned by the tag or well each point name contains.
- A folder of documents: suggested as searchable sections that mention tags.
"""

import csv
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fakeplant import schedules
from fuzer.core.pack import load_pack
from fuzer.graph import ontology as model
from fuzer.ops import pipeline

pytestmark = pytest.mark.sequence


@pytest.fixture(scope="module")
def ui(okan_db):
    from fuzer import api

    with TestClient(api.app) as client:
        yield okan_db.conn, client, okan_db.data


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def add_source(client, system: str, path: Path, table: str, values: dict | None = None) -> tuple[dict, dict, int]:
    """Steps 1 to 5 of Add a source: system, folder connection, table, preview (setting a date format when hinted),
    then save and load with the suggested mapping as it is offered."""
    ok(client.post("/sources/systems", json={"name": system, "values": {"owner": "ana"}, "by": "ana"}))
    c = ok(client.post(f"/sources/systems/{system}/connections",
                       json={"name": "export", "type": "folder", "config": {"path": str(path)}, "by": "ana"}))
    assert ok(client.post(f"/sources/connections/{c['id']}/test"))["ok"]
    tables = ok(client.get(f"/sources/connections/{c['id']}/tables"))["tables"]
    assert {"table": table, "dataset": None} in tables
    p = ok(client.get(f"/sources/connections/{c['id']}/tables/{table}"))
    if p["date_hint"]:  # the page's "Set it" button
        ok(client.patch(f"/sources/systems/{system}", json={"values": {"date_format": p["date_hint"]["date_format"]},
                                                             "by": "ana", "reason": "dates in the export's local format"}))
        p = ok(client.get(f"/sources/connections/{c['id']}/tables/{table}"))
        assert p["date_hint"] is None
    form = ok(client.post("/sources/mapper/form", json={"yaml": p["suggested"]["yaml"]}))
    assert form["warnings"] == [], form["warnings"]  # nothing the ontology doesn't declare or allow
    out = ok(client.post(f"/sources/connections/{c['id']}/tables/{table}",
                         json={"yaml": p["suggested"]["yaml"], "values": values or {}, "by": "ana"}))
    return p, out, c["id"]


def graph(conn, kind: str, source: str) -> dict:
    """Links from the entities of `kind` that `source` made: (link type, target kind) -> count."""
    return {(r["rel"], r["dst_kind"]): r["n"] for r in conn.execute(
        """select e.rel, d.kind as dst_kind, count(*) as n from edge e join entity s on s.fuzer_id = e.src
           join entity d on d.fuzer_id = e.dst
           where s.kind = %s and s.retired_at is null
             and exists (select 1 from crosswalk x where x.fuzer_id = s.fuzer_id and x.source = %s)
           group by 1, 2""", (kind, source))}


def test_project_schedules_from_the_ui_alone(ui):
    conn, client, data = ui
    lake = Path(tempfile.mkdtemp(prefix="fzui"))
    schedules.write(data, lake)

    p, out, cid = add_source(client, "planning", lake, "project_schedules", {"load_mode": "snapshot"})
    cols = p["suggested"]["columns"]
    assert p["suggested"]["key"] == "activity_id"
    assert cols["parent_id"]["meaning"] == "parent" and cols["location"]["meaning"] == "location"
    assert cols["start"]["meaning"] == "timestamp" and cols["owner"]["meaning"] == "person"
    text = p["suggested"]["yaml"]
    assert "kind: activity" in text and "rel: located_at" in text and "rel: executes" in text
    assert "refers_to" not in text

    # the first load is held: the export repeats an activity id
    assert out["load"]["held"] == ["project_schedules"] and out["health"]["state"] == "held"
    assert out["run"]["detail"]["checks"][0]["kind"] == "duplicate keys"

    # the planner fixes the export; Load again
    schedules.write(data, lake, fixed=True)
    again = ok(client.post("/sources/datasets/project_schedules/run", params={"full": "true"}))
    assert again["loaded"] == ["project_schedules"] and again["inserted"] >= 20
    d = ok(client.get("/sources/datasets/project_schedules"))
    assert d["runs"][0]["status"] == "done" and d["dataset"]["connection_id"] == cid
    assert d["mappings"][0]["current"] and d["mappings"][0]["author"] == "ana"

    pipeline.rebuild(conn, load_pack("okan"))  # what the page's Rebuild now requests
    links = graph(conn, "activity", "planning")
    assert links[("part_of", "activity")] >= 15  # activities under their project
    assert links[("executes", "work_order")] >= 3  # the turnaround's SAP orders
    assert links.get(("located_at", "tag"), 0) >= 5  # tags written 20-K-2200B, 20K2200B, OKAN-20-K2200B, 20 K 2200B
    assert links.get(("located_at", "facility")) and links.get(("located_at", "well"))
    assert not any(rel == "refers_to" for rel, _ in links)
    m = model.model(conn)
    assert m["needs_definition"] == {"kinds": [], "relations": [], "not_allowed": []}
    flare = conn.execute("""select count(*) as n from edge e join crosswalk x on x.fuzer_id = e.src
                            where x.source = 'planning' and x.source_key = 'OK-MOC-0233' and e.rel = 'located_at'""").fetchone()
    assert flare["n"] == 0  # FLARE-TIP-9 names nothing Fuzer knows: no link is guessed

    # a fortnight later: progress moves, a finish slips, the cancelled flare tip job is gone
    schedules.write(data, lake, update=True, fixed=True)
    upd = ok(client.post("/sources/datasets/project_schedules/run"))
    assert upd["loaded"] == ["project_schedules"] and upd["updated"] >= 3 and upd["removed"] == 1
    left = conn.execute("select 1 from source_record where source = 'planning' and source_key = 'OK-MOC-0233'").fetchone()
    assert left is None
    conn.commit()


def test_a_historian_point_list_from_the_ui_alone(ui):
    conn, client, data = ui
    folder = Path(tempfile.mkdtemp(prefix="fzuipts"))
    with (data / "pi_points.csv").open() as f:
        points = list(csv.DictReader(f))[:40]
    with (folder / "scada_points.csv").open("w", newline="") as f:  # another historian's export of the same points
        w = csv.DictWriter(f, fieldnames=["Point Name", "Description", "EU"])
        w.writeheader()
        w.writerows({"Point Name": r["tag"].replace(".PV", ".CV"), "Description": r["descriptor"], "EU": r["engunits"]}
                    for r in points)

    p, out, _ = add_source(client, "scada", folder, "scada_points")
    assert p["suggested"]["columns"]["Point Name"]["meaning"] == "point"
    text = p["suggested"]["yaml"]
    assert "shape: timeseries" in text and "kind: timeseries" in text and "rel: has_timeseries" in text
    assert "uom" in text  # the unit lands where the time series providers read it
    assert out["load"]["loaded"] == ["scada_points"] and out["load"]["inserted"] == 40

    pipeline.rebuild(conn, load_pack("okan"))
    owners = {r["kind"]: r["n"] for r in conn.execute(
        """select s.kind, count(*) as n from edge e join entity s on s.fuzer_id = e.src join entity t on t.fuzer_id = e.dst
           join crosswalk x on x.fuzer_id = t.fuzer_id and x.source = 'scada'
           where e.rel = 'has_timeseries' group by 1""")}
    assert owners.get("tag", 0) + owners.get("well", 0) >= 30
    conn.commit()


def test_a_folder_of_documents_from_the_ui_alone(ui):
    conn, client, _ = ui
    folder = Path(tempfile.mkdtemp(prefix="fzuidocs")) / "procedures"
    folder.mkdir()
    (folder / "k2100c-restart.md").write_text(
        "# Train C compressor restart procedure\n\n## 1 Before you start\nConfirm 20-K-2100C is isolated and the "
        "lift gas header 20-PT-3003 is stable.\n\n## 2 Restart\nOpen 20-XV-3100 slowly, watch 20-VT-2100C vibration.\n")
    (folder / "well-unloading.txt").write_text(
        "1 Purpose\nUnloading wells on jacket J12.\n\n2 Steps\nStart with OK-J12-04, then OK-J12-07.\n")

    p, out, cid = add_source(client, "edocs", folder, "procedures")
    assert p["connector"] == "documents" and p["sample"][0]["file"] == "k2100c-restart.md"
    assert p["sample"][0]["title"] == "Train C compressor restart procedure"
    text = p["suggested"]["yaml"]
    assert "shape: documents" in text and "document_part" in text and "mentions" in text
    assert out["load"]["loaded"] == ["procedures"] and out["load"]["inserted"] == 6  # each document and its two sections

    pipeline.rebuild(conn, load_pack("okan"))
    links = graph(conn, "document_section", "edocs")
    assert links[("part_of", "document")] == 4
    assert links.get(("mentions", "tag"), 0) >= 3 and links.get(("mentions", "well"), 0) >= 1
    found = conn.execute("""select title, heading from knowledge_index where kind = 'document_section'
                            and body ilike '%%20-VT-2100C%%'""").fetchall()
    assert found and found[0]["title"] == "Train C compressor restart procedure"

    # a new file arrives: it is in the next load
    (folder / "flare.md").write_text("# Flare tip\n\n## 1 Inspection\nInspect the flare tip yearly.\n")
    assert ok(client.post("/sources/datasets/procedures/run"))["inserted"] == 2
    assert ok(client.get(f"/sources/connections/{cid}/tables"))["tables"][0]["dataset"] == "procedures"
    conn.commit()
