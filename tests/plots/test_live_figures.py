"""Live figures carry their own wording and no colours, so any page can show them."""

import json
from datetime import date

import pytest

from fuzer.analysis.production_efficiency import calculate
from fuzer.plots import ChartError, analytics
from fuzer.plots.analytics import constraint_label, flow_capacity, production_efficiency

NOTICE = "Test notice."


def point(**changes):
    return {"date": "2026-10-14", "dynamic_max_sm3d": 100, "allocated_sm3d": 80, "gap_sm3d": 20, "efficiency_pct": 80,
            "active_constraint_id": "a", "binding_constraint_ids": ["a"], "capacity_basis": "declared_limits",
            "flags": [], "source_ids": ["r1"], **changes}


CONSTRAINTS = [{"id": "a", "label": "Export", "category": "export"}, {"id": "b", "label": "Compressor", "category": "equipment"}]


def efficiency():
    limit = {"date": "2026-10-13", "id": "a", "label": "Export", "record": "x", "value": 100, "unit": "Sm3/d",
             "qualifier": None, "category": "export"}
    out = calculate([{"id": "r", "date": "2026-10-13", "allocated": 80, "potential": 120}], [limit], date(2026, 10, 14))
    return {**out, "facility": {"id": "f", "label": "Plant"}, "source": "Reports"}


def stage(label, capacity, basis, grade=None, binding=False, undeclared=0):
    return {"id": label, "label": label, "capacity_sm3d": capacity, "basis": basis, "grade": grade, "binding": binding,
            "members": 2, "undeclared": undeclared, "undeclared_examples": ["A", "B"][:undeclared]}


def capacity(stages, **system):
    return {
        "as_of": "2026-10-14", "unit": "Sm3/d", "stages": stages, "facility": {"id": "f", "label": "Plant"}, "source": "Network",
        "system": {"limit_sm3d": 100, "allocated_sm3d": 90, "basis": "reported_potential", "established": False,
                   "binding_stages": [], **system},
        "demonstrated": {"peak_sm3d": 8198, "date": "2026-08-25", "since": "2025-10-15"},
        "well_tests": {"total_sm3d": 9528, "wells": 3, "first": "2026-10-01", "last": "2026-10-02", "not_tested": 1, "since": "2026-07-17"},
        "required_metadata": "Dated capacity", "warnings": [],
    }


def trace(fig, name):
    return next(t for t in fig.data if t["name"].startswith(name))


@pytest.mark.parametrize("fig", [
    production_efficiency(efficiency(), NOTICE),
    flow_capacity(capacity([stage("Wells", 130, "well_tests"), stage("HP", 90, "declared", "moc", True),
                            stage("LP", 95, "declared", "design"), stage("Exp", 110, "demonstrated", undeclared=2)]), NOTICE),
])
def test_a_spec_holds_no_colours(fig):
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                assert key not in ("color", "fgcolor", "bgcolor", "colorway", "colorscale"), key
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(fig.spec)
    assert NOTICE in fig.summary["context"]


def test_a_series_keeps_its_colorway_position_when_it_has_no_data():
    fig = flow_capacity(capacity([stage("HP", 90, "declared", "moc")]), NOTICE)
    assert [t.get("showlegend") for t in fig.data] == [True, True, False, False, False]
    assert fig.data[1]["offsetgroup"] == "system"
    assert all(t["offsetgroup"] == "stage" for i, t in enumerate(fig.data) if i != 1)


def test_wording_follows_the_data():
    open_case = capacity([stage("Wells", 130, "well_tests"), stage("HP", 110, "demonstrated", undeclared=2)])
    fig = flow_capacity(open_case, NOTICE)
    assert fig.summary["headline"].startswith("Bottleneck not established")
    assert any("9,528" in note and "1 well has no test" in note for note in fig.summary["notes"])
    assert any("8,198" in note for note in fig.summary["notes"])
    assert fig.summary["tables"][0]["rows"] == [["HP", "2 of 2", "A, B"]]
    assert trace(fig, "Demonstrated")["text"] == ["", "≥110"]

    done = flow_capacity(capacity([stage("Wells", 130, "reported_potential"), stage("HP", 90, "declared", "moc", True)],
                                  basis="declared_limits", established=True, limit_sm3d=90, binding_stages=["HP"]), NOTICE)
    assert done.summary["headline"] == "Bottleneck: HP at 90 Sm3/d. Every stage has a capacity."
    assert done.summary["tables"] == []
    assert not any("≥" in note or "well test" in note for note in done.summary["notes"])
    assert trace(done, "Approved")["text"] == ["130", "◆ 90"]
    assert trace(done, "System bottleneck")["y"] == [90, 90]


def test_a_partial_declaration_claims_no_bottleneck():
    fig = flow_capacity(capacity([stage("HP", 90, "declared", "moc", True), stage("LP", 110, "demonstrated", undeclared=2)],
                                 basis="declared_limits", limit_sm3d=90, binding_stages=["HP"]), NOTICE)
    assert fig.summary["headline"].endswith("so the bottleneck is not established.")
    assert trace(fig, "Lowest declared bound")


def test_design_values_are_their_own_series_and_unknown_stays_empty():
    fig = flow_capacity(capacity([stage("LP", 95, "declared", "design"), stage("Odd", None, "unknown", undeclared=2)]), NOTICE)
    assert trace(fig, "Original design")["y"] == [95, None]
    assert trace(fig, "Original design")["marker"]["pattern"]["shape"] == "."
    assert all(t["text"][1] == "" for t in fig.data if not t["name"].startswith("Reported"))


def test_no_network_says_so_without_a_plot():
    fig = flow_capacity(capacity([]), NOTICE)
    assert fig.data == []
    assert "No flow network" in fig.summary["headline"]


def test_stage_labels_are_escaped_for_plotly():
    fig = flow_capacity(capacity([stage("<b>x", 90, "declared", "moc")]), NOTICE)
    assert fig.data[0]["x"] == ["&lt;b&gt;x"]


def test_production_keeps_gaps_and_names_ties():
    fig = production_efficiency(efficiency(), NOTICE)
    assert len(fig.data[0]["x"]) == 14
    assert fig.data[0]["y"][12] == 100
    assert fig.data[0]["y"][0] is None
    assert fig.data[0]["connectgaps"] is False
    assert fig.data[2]["yaxis"] == "y2", "the gap has its own fitted axis so it shows at ~100% efficiency"
    assert fig.summary["stats"][0]["label"].startswith("Unrealized gap")
    assert fig.layout["yaxis"]["rangemode"] == "tozero", "A rate axis starts at zero, so small moves stay small"
    assert "Binding: Export [export]" in fig.data[0]["text"][12]
    assert fig.summary["tables"][0]["rows"][0][1] == "Unknown"
    tied = point(binding_constraint_ids=["b", "a"])
    assert constraint_label(tied, CONSTRAINTS) == "Export [export] + Compressor [equipment] (tied)"
    assert constraint_label(point(capacity_basis="reported_potential", binding_constraint_ids=[], active_constraint_id=None), CONSTRAINTS) \
        == "Reported potential · physical constraint unknown"
    assert constraint_label(point(dynamic_max_sm3d=None), CONSTRAINTS) == "Unknown"


def test_every_live_figure_builds_on_okan_and_passes_the_checks(okan_db):
    from fuzer.core.pack import load_pack

    pack = load_pack("okan")
    facility = okan_db.conn.execute("select distinct dst from edge where rel = 'reports_on'").fetchone()["dst"]
    for kind in analytics.FIGURES:
        out = analytics.live(okan_db.conn, pack, kind, facility)
        assert json.dumps(out)
        assert pack.data_notice in out["summary"]["context"]


def test_an_unknown_kind_is_refused():
    with pytest.raises(ChartError):
        analytics.live(None, None, "nope", "f")
