"""The business plan export in the demo data (step 9 of docs/work-model.md): `fakeplant generate` writes the lines
beside the SAP exports they plan, the built-in business plan source loads them like any export, and after the register
build they are intent lines matched to the cases doing the work, so Plan against actual reads plan, actual and variance.
A site with no business plan is in test_report_unknowns."""

import shutil
import tempfile
from datetime import date
from pathlib import Path

import pytest

from fakeplant import business_plan as export
from fuzer.core.pack import current_pack, load_pack
from fuzer.ops import business_plan, pipeline, report_pages
from fuzer.sources import ingest

pytestmark = pytest.mark.integration

DAY = date(2025, 12, 31)  # the day Okan's exports were written: its last quarter is planned and under way


@pytest.fixture(scope="module")
def planned(okan_db):
    """Okan with its business plan export beside the rest, as `fakeplant generate` writes it, loaded through the
    built-in source and followed by the register build."""
    data = Path(tempfile.mkdtemp(prefix="fzbp"))
    shutil.copytree(okan_db.data, data, dirs_exist_ok=True)
    wrote = export.write(data)
    conn = okan_db.conn
    assert ingest.ingest(conn, data, exports=["business_plan_lines"])["loaded"] == ["business_plan_lines"]
    pipeline.rebuild(conn, load_pack("okan"))
    out = pipeline.follow_register(conn, load_pack("okan"))["plan"]["business_plan"]
    conn.commit()
    return okan_db, wrote, out


def test_the_export_s_lines_are_intent_lines_under_their_plan_and_programs(planned):
    plant, wrote, out = planned
    conn = plant.conn
    assert wrote["lines"] >= 20 and out["lines"] == wrote["lines"] and out["skipped"] == 0 and out["matched"] > 0
    plan = conn.execute("select * from work_container where kind = 'business_plan'").fetchone()
    assert plan["title"] == "OKAN business plan"
    programs = {r["title"] for r in conn.execute("select title from work_container where parent_id = %s", (plan["id"],))}
    assert {"Maintenance", "Inspection", "Turnaround OK-TAR-2026"} <= programs
    lines = conn.execute("select * from work_plan_line where source_key is not null").fetchall()
    assert len(lines) == wrote["lines"] and all(r["subjects"] for r in lines)  # each on the area it plans
    assert len(business_plan.against_actual(conn, plan["id"])["lines"]) == wrote["lines"]  # its programs' lines too
    again = business_plan.sync(conn)
    assert again["added"] == 0 and again["dropped"] == 0 and again["matched"] == 0  # once
    conn.commit()


def test_plan_against_actual_reads_plan_actual_and_variance(planned):
    plant, _, _ = planned
    conn, pack = plant.conn, current_pack()
    v = report_pages.view(conn, pack, "plan", window="qtd", at=DAY)
    assert v["unknown"] == []
    got = {p: report_pages.panel_data(conn, pack, "plan", p, window="qtd", at=DAY)
           for p in ("est-hours", "act-hours", "est-cost", "act-cost")}
    assert all(got[p]["value"] and not got[p]["unknown"] for p in ("est-hours", "act-hours", "est-cost")), got
    # Okan's open orders carry no cost until SAP settles them: none recorded yet, which is not unknown
    assert not got["act-cost"]["unknown"] and (got["act-cost"]["value"] or got["act-cost"]["note"])
    table = report_pages.panel_data(conn, pack, "plan", "plan-lines", window="qtd", at=DAY)
    cells = [r["cells"] for r in table["rows"]]
    assert cells and not table["unknown"]
    assert {"var_h", "var_cost"} <= {c["key"] for c in table["columns"]}
    matched = [c for c in cells if c["cases"]]
    assert matched and all(c["var_h"] == round(c["act_h"] - c["est_h"], 1) for c in matched)
    assert any(c["cases"] == 0 for c in cells)  # planned, nothing started yet
    by_program = report_pages.panel_data(conn, pack, "plan", "by-program", window="qtd", at=DAY)
    assert {r["cells"]["label"] for r in by_program["rows"]} >= {"Maintenance"}


def test_the_hand_written_bundles_ship_their_business_plan_export():
    from fuzer.resources import BUNDLES

    for name in ("kestrel", "harrier"):
        folder = BUNDLES / name / "exports"
        shipped = (folder / export.FILE).read_text()
        rows = export.export(folder)
        assert rows and shipped.count("\n") == len(rows) + 1, name  # current with its own SAP exports
        assert all(r["period_start"].startswith("today") for r in rows)  # relative dates: never stale
