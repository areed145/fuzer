"""Kestrel ships a business plan export beside its SAP exports (relative dates, never stale), loaded by the same
built-in source as Okan's: after the register build its Plan against actual report reads plan, actual and variance
for the quarter to date, the actual cost from what SAP records on the orders."""

from fuzer.core import pack as packs
from fuzer.ops import business_plan, pipeline, report_pages


def test_kestrel_s_plan_against_actual_reads_its_business_plan(kestrel_db):
    conn = kestrel_db
    pack = packs.current_pack(conn)
    out = pipeline.follow_register(conn, pack)["plan"]["business_plan"]
    assert out["lines"] >= 10 and out["matched"] > 0 and business_plan.loaded(conn)
    assert report_pages.view(conn, pack, "plan")["unknown"] == []
    got = {p: report_pages.panel_data(conn, pack, "plan", p) for p in ("est-hours", "act-hours", "est-cost", "act-cost")}
    assert all(r["value"] and not r["unknown"] for r in got.values()), {k: (r["value"], r["note"]) for k, r in got.items()}
    lines = report_pages.panel_data(conn, pack, "plan", "plan-lines")["rows"]
    assert any(r["cells"]["var_h"] is not None and r["cells"]["cases"] for r in lines)
    assert any(r["cells"]["var_cost"] is not None for r in lines)
    conn.commit()
