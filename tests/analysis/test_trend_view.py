"""Trend views (P5.12a): a compressor's values grouped by its class's template, from measurement roles, never tag
names; the generic template for anything else."""

import pytest

from fuzer.analysis import trend_view


@pytest.fixture(scope="module")
def okan(okan_db):
    return okan_db.conn


def _id(conn, label):
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def _compressor(conn):
    """Any compressor with a driver: found by class and the drives edge, not by name."""
    return conn.execute(
        """
        select c.src as id from edge c join entity_label l on l.fuzer_id = c.dst
        join edge d on d.dst = c.src and d.rel = 'drives'
        where c.rel = 'classified_as' and l.label like 'ISO14224:CO%%' order by 1 limit 1
        """).fetchone()["id"]


def test_every_template_names_known_relations_and_roles():
    t = trend_view.templates()
    assert "CO" in t["classes"] and t["generic"]["shown"] >= 1
    assert trend_view.parse("driver.speed") == ("driver", "speed")
    assert trend_view.parse("speed") == ("self", "speed")


def test_a_compressor_opens_on_its_template_groups(okan):
    v = trend_view.view(okan, _compressor(okan))
    assert v["template"] == {"key": "CO", "title": "Compressor"}
    groups = {g["key"]: g for g in v["groups"]}
    assert {"pressures", "temperatures", "driver", "vibration"} <= set(groups)
    roles = {g: {t["role"] for t in groups[g]["traces"]} for g in groups}
    assert "discharge_pressure" in roles["pressures"]
    assert "discharge_temperature" in roles["temperatures"]
    assert roles["vibration"] & {"vibration", "shaft_vibration"}
    # the speed comes from its driver, and the suction from what is upstream of it
    driver = [t for t in groups["driver"]["traces"] if t["on"]["relation"] == "driver"]
    assert driver and all(t["role"] in ("speed", "power", "running_status") for t in driver)
    assert any(t["on"]["relation"] == "upstream" for t in groups["pressures"]["traces"])
    # every trace carries its values, point, units and the axis it shares
    for g in v["groups"]:
        for t in g["traces"]:
            assert t["values"] and t["point"] and t["axis"] and t["show"] == g["show"]
    assert v["provider"] and v["centre"] and v["step_minutes"]
    assert not v["missing"], v["missing"]


def test_groups_switched_off_and_points_no_group_asked_for_are_kept_off(okan):
    v = trend_view.view(okan, _compressor(okan))
    off = [g for g in v["groups"] if not g["show"]]
    assert off and all(not t["show"] for g in off for t in g["traces"])
    assert all(not t["show"] for t in v["other"])
    ids = [t["id"] for g in v["groups"] for t in g["traces"]] + [t["id"] for t in v["other"]]
    assert len(ids) == len(set(ids))


def test_an_expected_role_the_unit_lacks_is_named(okan, monkeypatch):
    t = trend_view.templates()
    co = {**t["classes"]["CO"], "expect": [["interstage_pressure"]]}
    monkeypatch.setitem(t["classes"], "CO", co)
    v = trend_view.view(okan, _compressor(okan))
    assert v["missing"] == [{"role": "interstage_pressure", "role_label": "Interstage pressure", "relation": "self"}]


def test_a_class_without_a_template_is_grouped_by_what_it_measures(okan):
    pump = okan.execute(
        """
        select c.src as id from edge c join entity_label l on l.fuzer_id = c.dst
        where c.rel = 'classified_as' and l.label like 'ISO14224:PU%%'
          and exists (select 1 from measurement_role r where r.asset = c.src) order by 1 limit 1
        """).fetchone()["id"]
    v = trend_view.view(okan, pump)
    assert v["template"]["key"] == "generic"
    keys = [g["key"] for g in v["groups"]]
    assert "pressure" in keys
    assert [g["show"] for g in v["groups"]][:3] == [True] * min(3, len(keys))


def test_a_timeseries_is_its_own_trend(okan):
    pid = okan.execute("select point_id from measurement_role order by point_id limit 1").fetchone()["point_id"]
    v = trend_view.view(okan, pid)
    assert [t["id"] for g in v["groups"] for t in g["traces"]] == [pid]


def test_unknown_entity(okan):
    assert trend_view.view(okan, "fz-nope") == {"error": "unknown entity"}
