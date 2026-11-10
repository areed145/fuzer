from datetime import date

import pytest

from fuzer.analysis.flow_capacity import calculate, flow_capacity, stages
from fuzer.analysis.production_efficiency import production_efficiency

END = date(2026, 11, 5)
node = lambda id, type, description=None: {"id": id, "label": id, "type": type, "description": description}
NODES = {n["id"]: n for n in [
    node("w1", "well"), node("w2", "well"),
    node("m1", "manifold"), node("m2", "manifold"),
    node("sa", "separator", "Satellite"), node("sb", "separator", "Satellite B"),
    node("hp", "separator", "HP separator"), node("lp", "separator", "LP separator"),
    node("scrub", "separator", "Gas scrubber"),
    node("ka", "compressor"), node("kb", "compressor"),
    node("oil-meter", "meter"), node("gas-meter", "meter"),
    node("term", "sales_point", "Crude terminal"), node("plant", "sales_point", "Gas plant"),
]}
EDGES = [("w1", "m1"), ("w2", "m2"), ("m2", "m1"), ("m1", "sa"), ("m2", "sb"), ("sa", "hp"), ("sb", "hp"),
         ("hp", "lp"), ("lp", "oil-meter"), ("oil-meter", "term"), ("hp", "scrub"), ("scrub", "ka"), ("scrub", "kb"),
         ("ka", "gas-meter"), ("kb", "gas-meter"), ("gas-meter", "plant")]


def latest(limit=100, basis="reported_potential", binding=(), flags=()):
    return {"dynamic_max_sm3d": limit, "allocated_sm3d": 90, "capacity_basis": basis,
            "binding_constraint_ids": list(binding), "flags": list(flags)}


def bound(id, value, unit="Sm3/d", qualifier=None):
    return {"id": id, "label": id, "record": f"{id}:r", "value": value, "unit": unit, "qualifier": qualifier}


def report(day, oil):
    return {"id": f"d{day}", "date": day, "allocated": oil}


def run(limits=(), tests=(), reports=(), state=None, nodes=NODES, edges=EDGES):
    return calculate(nodes, edges, list(limits), list(tests), list(reports), state or latest(), END)


def by_label(out):
    return {row["label"]: row for row in out["stages"]}


def test_stages_follow_the_network_not_a_fixed_list():
    labels = [s["label"] for s in stages(NODES, EDGES)]
    assert labels[:4] == ["Wells", "Gathering manifolds", "Field separators", "HP separator"]
    assert labels.index("Compression") > labels.index("Gas scrubber")
    assert set(labels[-2:]) == {"Crude terminal", "Gas plant"}
    assert len(labels) == 9


def test_parallel_equipment_shares_a_stage_and_a_gas_branch_gets_its_own():
    grouped = {s["label"]: {m["id"] for m in s["members"]} for s in stages(NODES, EDGES)}
    assert grouped["Field separators"] == {"sa", "sb"}
    assert grouped["Compression"] == {"ka", "kb"}
    assert grouped["LP separator"] == {"lp"}
    assert grouped["Gas scrubber"] == {"scrub"}
    assert grouped["Crude terminal"] == {"oil-meter", "term"}


def test_cycles_in_the_source_terminate():
    assert stages(NODES, [*EDGES, ("lp", "hp")])


def test_no_declared_capacity_uses_labeled_evidence_and_names_what_is_missing():
    tests = [{"fuzer_id": "w1", "at": "2026-11-01", "value": 70}, {"fuzer_id": "w2", "at": "2026-11-02", "value": 60}]
    out = run(tests=tests, reports=[report("2026-09-01", 110), report("2026-10-01", 95)])
    stage = by_label(out)
    assert (stage["Wells"]["capacity_sm3d"], stage["Wells"]["basis"]) == (130, "well_tests")
    assert (stage["Compression"]["capacity_sm3d"], stage["Compression"]["basis"]) == (110, "demonstrated")
    assert stage["Compression"]["undeclared"] == 2
    assert set(stage["Compression"]["undeclared_examples"]) == {"ka", "kb"}
    assert out["demonstrated"] | {"since": None} == {"peak_sm3d": 110, "date": "2026-09-01", "since": None}
    assert out["system"]["limit_sm3d"] == 100
    assert out["system"]["established"] is False
    assert out["system"]["binding_stages"] == []
    assert out["well_tests"]["not_tested"] == 0
    assert "system_oil_capacity" in out["required_metadata"]


def test_every_stage_declared_establishes_the_bottleneck():
    limits = [bound(id, 500) for id in NODES if id not in ("ka", "kb")] + [bound("ka", 100), bound("kb", 200)]
    out = run(limits, state=latest(100, "declared_limits", ["ka"]))
    assert all(row["basis"] == "declared" and row["undeclared"] == 0 for row in out["stages"])
    assert by_label(out)["Compression"]["capacity_sm3d"] == 100
    assert by_label(out)["Compression"]["binding"] is True
    assert out["system"]["established"] is True
    assert out["system"]["binding_stages"] == ["Compression"]


def test_partial_declarations_do_not_claim_a_bottleneck():
    out = run([bound("ka", 100)], reports=[report("2026-09-01", 110)], state=latest(100, "declared_limits", ["ka"]))
    assert by_label(out)["Compression"]["basis"] == "declared"
    assert by_label(out)["Compression"]["undeclared"] == 0  # a bound already covers its parallel trains
    assert by_label(out)["LP separator"]["basis"] == "demonstrated"
    assert out["system"]["established"] is False
    assert out["system"]["binding_stages"] == ["Compression"]


@pytest.mark.parametrize("limits", [
    [bound("hp", -1)], [bound("hp", 100, unit="kSm3/d")], [bound("hp", 100, qualifier="<")],
    [bound("hp", 100), bound("hp", 120)],
])
def test_invalid_or_ambiguous_bounds_are_not_capacity(limits):
    out = run(limits, reports=[report("2026-09-01", 110)])
    assert by_label(out)["HP separator"]["basis"] == "demonstrated"


def test_invalid_bound_flag_warns():
    out = run(state=latest(None, "missing", flags=["invalid_capacity_limit"]))
    assert out["system"]["limit_sm3d"] is None
    assert "withheld" in out["warnings"][0]


def test_bound_off_the_network_still_appears():
    out = run([bound("fz-area", 80)], state=latest(80, "declared_limits", ["fz-area"]))
    assert by_label(out)["fz-area"]["binding"] is True


def test_demonstrated_peak_skips_invalid_and_ambiguous_days():
    out = run(reports=[report("2026-09-01", -24745), report("2026-09-02", None),
                       report("2026-09-03", 500), report("2026-09-03", 600), report("2026-09-04", 90)])
    assert out["demonstrated"]["peak_sm3d"] == 90
    assert out["demonstrated"]["date"] == "2026-09-04"


def test_no_evidence_stays_unknown():
    out = run()
    assert {row["basis"] for row in out["stages"]} == {"unknown"}
    assert all(row["capacity_sm3d"] is None for row in out["stages"])


def test_no_network_says_so():
    out = run(nodes={}, edges=[])
    assert out["stages"] == []
    assert "network" in out["warnings"][0]


def test_okan_network_gives_the_real_stages_and_agrees_with_the_production_chart(okan_db):
    facility = okan_db.conn.execute(
        """select e.fuzer_id from entity e join entity_label l using (fuzer_id)
           where e.kind = 'facility' and e.retired_at is null and e.merged_into is null
             and exists (select 1 from edge r where r.dst = e.fuzer_id and r.rel = 'reports_on')""",
    ).fetchone()["fuzer_id"]
    out = flow_capacity(okan_db.conn, facility)
    labels = [row["label"] for row in out["stages"]]
    for expected in ["Wells", "Gathering manifolds", "Field separators", "HP Production Separator",
                     "LP Production Separator", "Compression"]:
        assert expected in labels
    assert labels.index("Field separators") < labels.index("HP Production Separator") < labels.index("Compression")
    efficiency = production_efficiency(okan_db.conn, facility)
    assert out["as_of"] == efficiency["end"]
    assert out["system"]["limit_sm3d"] == efficiency["points"][-1]["dynamic_max_sm3d"]
