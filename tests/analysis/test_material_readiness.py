"""Material readiness (#362): the parts reserved for open work orders against stock and open purchase orders, earliest
need first. On Okan the planted order due after its job's need date is flagged late with the days, and the part with
nothing on order and a planned delivery time past the need date is flagged too late to order; a delivery date is the
order's own and nothing is said beyond the record."""

import json

from fuzer.analysis import material_readiness as mr


def test_a_late_arrival_and_a_part_too_late_to_order_are_flagged(okan_db):
    conn = okan_db.conn
    key = json.loads((okan_db.data / "answer_keys" / "material_readiness.json").read_text())
    got = mr.coverage(conn)
    assert got["verdict"] == "late_parts" and got["lines"] and got["summary"]["lines"] == len(got["lines"])
    by = {(x["work_order"], x["material"]): x for x in got["lines"]}
    late = by[(key["late_order"]["work_order"], key["late_order"]["material"])]
    assert late["verdict"] == "late" and late["days_late"] == 5 and late["on_order"]
    assert f"{late['days_late']} days after the job needs it" in late["why"] and late["on_order"][0]["due"] in late["why"]
    short = by[(key["not_on_order"]["work_order"], key["not_on_order"]["material"])]
    assert short["verdict"] == "not_on_order_too_late" and not short["on_order"] and short["lead_time_days"] > 21
    assert short["earliest_if_ordered_now"] > short["need_date"] and "nothing on order" in short["why"]
    assert got["lines"][0]["verdict"] == "late"  # the worst first
    assert {x["verdict"] for x in got["lines"]} >= {"in_stock", "late", "not_on_order_too_late"}
    assert "nothing here says a part is ordered" in got["note"]
    # stock is allocated once: no material covers more lines from stock than it has
    for mat in {x["material_id"] for x in got["lines"]}:
        mine = [x for x in got["lines"] if x["material_id"] == mat]
        assert sum(x["from_stock"] for x in mine) <= mine[0]["stock"]
    one = mr.coverage(conn, ref=key["late_order"]["work_order"])
    assert {x["work_order"] for x in one["lines"]} == {key["late_order"]["work_order"]}


def test_the_tool_and_the_spares_screen_name_the_order_or_the_lead_time(okan_db):
    from fuzer.agents.capabilities import maintenance
    from fuzer.analysis import calcs

    got = maintenance.material_readiness()
    assert got["summary"]["late"] >= 1 and "error" in maintenance.material_readiness(ref="NO-SUCH-ORDER")
    conn = okan_db.conn
    field = conn.execute("select fuzer_id from entity where kind = 'facility' limit 1").fetchone()["fuzer_id"]
    cov = calcs.run(conn, "spares_coverage", field)
    detail = [d for u in cov["units"] for d in u.get("out_of_stock_detail", [])]
    assert detail and all(d["lead_time_days"] is not None for d in detail)
    assert any(d["on_order_due"] for d in detail) and "planned delivery time" in cov["note"]
