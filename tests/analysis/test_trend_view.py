"""Trend views (P5.12a): a compressor's values grouped by its class's template, from measurement roles, never tag
names; the generic template for anything else."""

from unittest import mock

import pytest

from fuzer.analysis import trend_view
from fuzer.core import trends


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
    from fuzer.core import trends

    t = trends.builtin()
    assert {"CO", "GT", "PU", "VE", "HE", "WELL"} <= set(t["classes"]) and t["generic"]["shown"] >= 1
    assert trends.parse("driver.speed") == ("driver", "speed")
    assert trends.parse("speed") == ("self", "speed")


def test_a_compressor_opens_on_its_template_groups(okan):
    v = trend_view.view(okan, _compressor(okan))
    assert v["template"] == {"key": "CO", "title": "Compressor", "source": "platform"}
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


class _Pack:
    def __init__(self, classes):
        self.trend_templates = {"classes": classes}


def test_an_expected_role_the_unit_lacks_is_named(okan):
    from fuzer.core import trends

    co = {**trends.builtin()["classes"]["CO"], "expect": [["interstage_pressure"]]}
    v = trend_view.view(okan, _compressor(okan), pack=_Pack({"CO": co}))
    assert v["missing"] == [{"role": "interstage_pressure", "role_label": "Interstage pressure", "relation": "self"}]


def test_a_bundle_replaces_a_class_template_without_code(okan):
    mine = {"title": "Machine", "groups": [{"key": "shaft", "label": "Shaft", "traces": ["shaft_vibration"]}]}
    v = trend_view.view(okan, _compressor(okan), pack=_Pack({"CO": mine}))
    assert v["template"] == {"key": "CO", "title": "Machine", "source": "bundle"}
    assert [g["key"] for g in v["groups"]] == ["shaft"]
    assert {t["role"] for t in v["groups"][0]["traces"]} == {"shaft_vibration"}
    assert v["other"], "the unit's other points stay reachable"


def test_a_bundle_template_naming_an_unknown_role_is_refused_when_the_pack_is_read():
    from fuzer.core import trends

    with pytest.raises(ValueError, match="isn't <relation>.<role>"):
        trends.checked({"classes": {"CO": {"title": "X", "groups": [{"key": "a", "label": "A", "traces": ["sideways.speed"]}]}}})
    with pytest.raises(ValueError, match="isn't <relation>.<role>"):
        trends.checked({"classes": {"CO": {"title": "X", "groups": [{"key": "a", "label": "A", "traces": ["warp_speed"]}]}}})


@pytest.mark.parametrize("code", ["GT", "PU", "VE", "HE", "WELL"])
def test_each_class_opens_on_its_own_template_with_nothing_it_expects_missing(okan, code):
    unit = okan.execute(
        """
        select r.asset as id from measurement_role r join entity e on e.fuzer_id = r.asset
        where (%(c)s = 'WELL' and e.kind = 'well') or exists (
            select 1 from edge c join entity_label l on l.fuzer_id = c.dst
            where c.src = r.asset and c.rel = 'classified_as' and l.label like 'ISO14224:' || %(c)s || '%%')
        group by r.asset order by count(*) desc, r.asset limit 1
        """, {"c": code}).fetchone()
    assert unit, f"Okan has a {code} with roles"
    v = trend_view.view(okan, unit["id"])
    assert v["template"]["key"] == code
    assert v["groups"] and all(t["values"] for g in v["groups"] for t in g["traces"])
    assert not v["missing"], v["missing"]


def test_a_class_without_a_template_is_grouped_by_what_it_measures(okan):
    """With the pump template taken away, a pump reads on the generic one."""
    pump = okan.execute(
        """
        select c.src as id from edge c join entity_label l on l.fuzer_id = c.dst
        where c.rel = 'classified_as' and l.label like 'ISO14224:PU%%'
          and exists (select 1 from measurement_role r where r.asset = c.src) order by 1 limit 1
        """).fetchone()["id"]
    without = {k: t for k, t in trends.builtin()["classes"].items() if k != "PU"}
    with mock.patch.object(trends, "builtin", return_value={**trends.builtin(), "classes": without}):
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
