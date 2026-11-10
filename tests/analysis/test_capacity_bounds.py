"""Okan's registers through the rebuild, against the generator's hidden truth."""

import json
from collections import Counter
from datetime import date, timedelta

import pytest

from fuzer.analysis.capacity_bounds import as_oil, stage_bound, standing, water_cut
from fuzer.analysis.flow_capacity import flow_capacity
from fuzer.analysis.production_efficiency import production_efficiency


def rating(id, value, at, method, quantity="liquid_capacity"):
    return {"id": id, "quantity": quantity, "at": date.fromisoformat(at), "value": value, "method": method}


def test_the_newest_approved_entry_stands_and_design_only_fills_in():
    rows = [rating("hp", 15900, "1998-06-01", "design"), rating("hp", 14500, "2025-02-03", "statement"),
            rating("hp", 13400, "2025-05-19", "moc"), rating("lp", 17000, "1998-06-01", "design")]
    day = date(2025, 3, 1)
    assert standing(rows, day)[("hp", "liquid_capacity")]["value"] == 14500
    assert standing(rows, date(2025, 6, 1))[("hp", "liquid_capacity")]["value"] == 13400
    assert standing(rows, date(2020, 1, 1))[("hp", "liquid_capacity")]["value"] == 15900
    assert standing(rows, day)[("lp", "liquid_capacity")]["method"] == "design"


def test_a_later_design_value_does_not_beat_an_approved_one():
    rows = [rating("hp", 14500, "2024-01-01", "statement"), rating("hp", 99999, "2025-01-01", "design")]
    assert standing(rows, date(2025, 6, 1))[("hp", "liquid_capacity")]["value"] == 14500


def test_ratings_become_oil_with_the_days_fluids():
    assert as_oil("oil_capacity", 100, 0.5, 80, 300, 200) == 100
    assert as_oil("liquid_capacity", 100, 0.25, 80, 300, 200) == 75
    assert as_oil("gas_capacity", 300, 0.5, 80, 300, 200) == 80
    assert as_oil("export_gas_capacity", 100, 0.5, 80, 300, 200) == 40
    assert as_oil("gas_capacity", 300, 0.5, 80, None, 200) is None
    assert water_cut({"a": (75, 25.0)}) == pytest.approx(0.25)


def stage(*members):
    return {"members": [{"id": id, "type": type} for id, type in members]}


def rated(*rows):
    return {(id, "oil_capacity"): {"value": value, "method": method} for id, value, method in rows}


oil = lambda quantity, value: value


def test_parallel_members_add_and_partial_stages_scale_to_the_facility():
    found = stage_bound(stage(("a", "compressor"), ("b", "compressor")), rated(("a", 100, "moc"), ("b", 50, "design")), 0.5, oil)
    assert found == (300, ["a", "b"], "design")


def test_series_types_take_the_tighter_and_an_unrated_parallel_member_voids_its_total():
    members = stage(("m", "meter"), ("t", "sales_point"))
    assert stage_bound(members, rated(("m", 120, "design"), ("t", 100, "statement")), 1, oil)[:2] == (100, ["t"])
    assert stage_bound(members, rated(("t", 100, "statement")), 1, oil)[:2] == (100, ["t"])
    assert stage_bound(stage(("a", "compressor"), ("b", "compressor")), rated(("a", 100, "moc")), 1, oil) is None


@pytest.fixture(scope="module")
def plant(okan_db):
    facility = okan_db.conn.execute(
        """select distinct e.dst as id from edge e where e.rel = 'reports_on'""").fetchone()["id"]
    return okan_db, facility


def test_okan_names_the_generators_bottleneck(plant):
    db, facility = plant
    out = flow_capacity(db.conn, facility)
    truth = {t["date"]: t for t in json.loads((db.data / "answer_keys" / "capacity.json").read_text())}[out["as_of"]]
    assert truth["binding"] == "hp_separator"
    assert out["system"]["established"] is True
    assert out["system"]["binding_stages"] == ["HP Production Separator"]
    assert out["system"]["limit_sm3d"] == pytest.approx(truth["limit_sm3d"], abs=1)
    assert all(row["undeclared"] == 0 for row in out["stages"])
    grades = {row["label"]: (row["basis"], row["grade"]) for row in out["stages"]}
    assert grades["Wells"] == ("reported_potential", None)
    assert grades["HP Production Separator"] == ("declared", "moc")
    assert grades["LP Production Separator"] == ("declared", "design")
    assert grades["Compression"] == ("declared", "statement")


def test_okan_history_respects_the_limits_and_the_binding_stage_moves(plant):
    db, facility = plant
    truth = json.loads((db.data / "answer_keys" / "capacity.json").read_text())
    assert set(Counter(t["binding"] for t in truth)) == {"wells", "hp_separator"}
    for day in ("2024-06-28", "2025-07-01", "2025-12-28"):
        points = production_efficiency(db.conn, facility, date.fromisoformat(day) + timedelta(days=2))["points"]
        expected = next(t for t in truth if t["date"] == day)
        point = next(p for p in points if p["date"] == day)
        assert point["dynamic_max_sm3d"] == pytest.approx(expected["limit_sm3d"], abs=1)
        assert ("reported_potential" in point["binding_constraint_ids"]) == (expected["binding"] == "wells")
        assert point["allocated_sm3d"] <= point["dynamic_max_sm3d"] + 1


def test_an_open_moc_never_sets_capacity(plant):
    db, _ = plant
    hp = db.conn.execute(
        """select max(m.value) as value from measurement m join entity_label l using (fuzer_id)
           where m.quantity = 'liquid_capacity' and l.label = '10-V-2001' and m.method = 'moc_open'""").fetchone()
    assert hp["value"] == 15500  # landed as a record
    bound = db.conn.execute(
        """select m.method from measurement m join entity_label l using (fuzer_id)
           where m.quantity = 'system_oil_capacity' and l.label = '10-V-2001' and m.at = '2025-12-30'""").fetchone()
    assert bound["method"] == "derived from moc"
