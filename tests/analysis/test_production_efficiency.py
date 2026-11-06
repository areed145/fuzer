import json
from datetime import date

import pytest
from psycopg.types.json import Jsonb

from fuzer.analysis.production_efficiency import calculate, production_efficiency
from fuzer.core import db

END = date(2026, 11, 5)


def report(day="2026-11-05", allocated=80, potential=120, id="report"):
    return {"date": day, "allocated": allocated, "potential": potential, "id": id}


def bound(id="separator", value=100, day="2026-11-05", **kwargs):
    return {"date": day, "id": id, "record": f"capacity:{id}:{day}", "value": value,
            "unit": "Sm3/d", "category": id, **kwargs}


def test_minimum_binding_ties_and_negative_gap_are_not_clamped():
    out = calculate([report(allocated=110)], [bound(), bound("export"), bound("wells", 120)], END)
    point = out["points"][-1]
    assert point["dynamic_max_sm3d"] == 100
    assert point["active_constraint_id"] == "export"
    assert point["binding_constraint_ids"] == ["export", "separator"]
    assert point["gap_sm3d"] == -10
    assert point["efficiency_pct"] == pytest.approx(110)
    assert point["flags"] == ["allocated_above_capacity", "tied_constraints"]
    assert len(out["constraints"]) == 3


def test_fourteen_days_including_leap_day_and_unknown_reported_potential_constraint():
    out = calculate([report("2024-02-29")], [], date(2024, 3, 1))
    assert out["since"] == "2024-02-17"
    assert out["until"] == "2024-03-02"
    assert len(out["points"]) == 14
    point = out["points"][-2]
    assert point["dynamic_max_sm3d"] == 120
    assert point["capacity_basis"] == "reported_potential"
    assert point["active_constraint_id"] is None
    assert point["binding_constraint_ids"] == []
    assert point["efficiency_pct"] == pytest.approx(80 / 120 * 100)
    assert out["points"][-1]["allocated_sm3d"] is None
    assert out["points"][-1]["dynamic_max_sm3d"] is None
    assert out["summary"]["paired_days"] == 1


def test_period_efficiency_uses_paired_volumes_not_mean_percentages():
    reports = [report("2026-11-03", 50, 100), report("2026-11-04", 180, 200),
               report(allocated=None, potential=1000)]
    out = calculate(reports, [], END)
    assert out["summary"] == {"allocated_sm3": 230, "dynamic_max_sm3": 300, "paired_days": 2,
                              "efficiency_pct": pytest.approx(230 / 300 * 100)}


@pytest.mark.parametrize("value", [None, -1, "bad", float("nan"), float("inf"), True])
def test_bad_allocation_is_not_zero(value):
    out = calculate([report(allocated=value)], [], END)
    assert out["points"][-1]["allocated_sm3d"] is None
    assert out["points"][-1]["efficiency_pct"] is None
    assert out["summary"]["paired_days"] == 0


@pytest.mark.parametrize("limit", [
    bound(value=-1), bound(value=float("nan")), bound(unit="kSm3/d"),
    bound(qualifier="<"), bound(value=True),
])
def test_invalid_bounds_do_not_silently_fall_back_to_potential(limit):
    point = calculate([report()], [bound("wells", 120), limit], END)["points"][-1]
    assert point["dynamic_max_sm3d"] is None
    assert "invalid_capacity_limit" in point["flags"]


def test_duplicate_reports_and_conflicting_bounds_are_ambiguous():
    point = calculate([report(), report(id="other")], [], END)["points"][-1]
    assert point["allocated_sm3d"] is None
    assert point["dynamic_max_sm3d"] is None
    assert "ambiguous_report" in point["flags"]
    point = calculate([report()], [bound(), bound(value=90)], END)["points"][-1]
    assert point["dynamic_max_sm3d"] is None
    assert "ambiguous_capacity_limit" in point["flags"]


def test_zero_capacity_is_real_and_has_no_efficiency_percentage():
    point = calculate([report(allocated=0)], [bound(value=0)], END)["points"][-1]
    assert point["dynamic_max_sm3d"] == 0
    assert point["gap_sm3d"] == 0
    assert point["efficiency_pct"] is None
    assert point["active_constraint_id"] == "separator"
    assert "zero_capacity" in point["flags"]


def test_derived_overflow_stays_json_safe_and_period_ratio_is_scaled():
    out = calculate([report(potential=1e-308)], [], END)
    assert out["points"][-1]["efficiency_pct"] is None
    assert "derived_metric_overflow" in out["points"][-1]["flags"]
    assert out["summary"]["efficiency_pct"] is None
    json.dumps(out, allow_nan=False)
    out = calculate([report("2026-11-04", 1e308, 1e308), report(allocated=1e308, potential=1e308)], [], END)
    assert out["summary"]["allocated_sm3"] is None
    assert out["summary"]["dynamic_max_sm3"] is None
    assert out["summary"]["efficiency_pct"] == 100
    json.dumps(out, allow_nan=False)


def test_daily_switches_and_no_capacity_carry_forward_or_future_leakage():
    out = calculate([report("2026-11-04"), report(), report("2026-11-06")],
                    [bound("separator", 100, "2026-11-04"), bound("export", 90)], END)
    assert out["points"][-2]["active_constraint_id"] == "separator"
    assert out["points"][-1]["active_constraint_id"] == "export"
    assert all(p["date"] <= END.isoformat() for p in out["points"])


@pytest.mark.parametrize("end", [date.min, date.max])
def test_date_boundaries_are_rejected(end):
    with pytest.raises(ValueError, match="fourteen daily samples"):
        calculate([], [], end)


@pytest.fixture
def graph():
    with db.connect() as conn:
        db.init_schema(conn)
        for id, kind in [("fz-pe-facility", "facility"), ("fz-pe-other", "facility"),
                         ("fz-pe-area", "area"), ("fz-pe-separator", "tag"),
                         ("fz-pe-export", "allocation_node"), ("fz-pe-day1", "production_day"),
                         ("fz-pe-day2", "production_day"), ("fz-pe-other-day", "production_day")]:
            conn.execute("insert into entity(fuzer_id, kind) values (%s, %s)", (id, kind))
            conn.execute("insert into entity_label values (%s, %s)", (id, id))
        for src, rel, dst, start, end in [
            ("fz-pe-day1", "reports_on", "fz-pe-facility", None, None),
            ("fz-pe-day2", "reports_on", "fz-pe-facility", None, None),
            ("fz-pe-other-day", "reports_on", "fz-pe-other", None, None),
            ("fz-pe-area", "part_of", "fz-pe-facility", None, None),
            ("fz-pe-export", "located_at", "fz-pe-area", None, None),
            ("fz-pe-separator", "flows_to", "fz-pe-export", "2026-11-04", "2026-11-05"),
            ("fz-pe-export", "flows_to", "fz-pe-separator", None, None),  # cycle
        ]:
            conn.execute("""insert into edge(src, rel, dst, valid_from, valid_to, sources, confidence)
                            values (%s, %s, %s, %s, %s, '{}', 1)""", (src, rel, dst, start, end))
        for id, day in [("fz-pe-day1", "2026-11-04"), ("fz-pe-day2", "2026-11-05"),
                        ("fz-pe-other-day", "2026-12-01")]:
            for field, value in [("report_date", day), ("oil_sm3", 80), ("oil_potential_sm3", 120)]:
                conn.execute("insert into golden values (%s, %s, %s, 'ec', 'test')", (id, field, Jsonb(value)))
        for id, day, value in [("fz-pe-separator", "2026-11-04", 100),
                               ("fz-pe-separator", "2026-11-05", 1),
                               ("fz-pe-export", "2026-11-05", 90),
                               ("fz-pe-other", "2026-11-05", 1)]:
            conn.execute("""insert into measurement(record, quantity, fuzer_id, at, value, unit)
                            values (%s, 'system_oil_capacity', %s, %s, %s, 'Sm3/d')""",
                         (f"{id}:{day}", id, day, value))
        try:
            yield conn
        finally:
            conn.rollback()


def test_graph_scope_dates_cycle_and_latest_facility_report(graph):
    out = production_efficiency(graph, "fz-pe-facility")
    assert out["end"] == "2026-11-05"  # not the other facility's December report
    assert out["points"][-2]["dynamic_max_sm3d"] == 100
    assert out["points"][-2]["active_constraint_id"] == "fz-pe-separator"
    assert out["points"][-1]["dynamic_max_sm3d"] == 90
    assert out["points"][-1]["active_constraint_id"] == "fz-pe-export"
    assert out["facility"]["id"] == "fz-pe-facility"
    assert out["points"][-1]["allocated_sm3d"] == 80
    assert len(out["points"]) == 14


def test_graph_rejects_non_facility(graph):
    with pytest.raises(ValueError, match="live facility"):
        production_efficiency(graph, "fz-pe-separator")


def test_existing_mapped_reports_supply_capacity_reference(okan_db):
    facility = okan_db.conn.execute(
        "select fuzer_id from entity where kind = 'facility' and retired_at is null limit 1",
    ).fetchone()["fuzer_id"]
    out = production_efficiency(okan_db.conn, facility)
    assert len(out["points"]) == 14
    assert out["summary"]["paired_days"] == 14
    assert all(p["capacity_basis"] == "reported_potential" for p in out["points"])
    assert all(p["active_constraint_id"] is None for p in out["points"])
    assert out["summary"]["efficiency_pct"] > 0
