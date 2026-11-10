"""Rule conditions and templates for the work a system brings (#465)."""

import pytest

from fuzer.sources import workflows
from fuzer.sources._catalog_common import CatalogError


@pytest.mark.parametrize("when, payload, expected", [
    ({"flow_hours": {"lt": 23}}, {"flow_hours": 22.2}, True),
    ({"flow_hours": {"lt": 23}}, {"flow_hours": "24.0"}, False),
    ({"flow_hours": {"lt": 23}}, {"flow_hours": None}, False),
    ({"status": {"eq": "E"}}, {"status": "E"}, True),
    ({"status": {"ne": "E"}}, {"status": None}, True),
    ({"status": {"in": ["E", "X"]}}, {"status": "X"}, True),
    ({"status": {"not_in": ["E"]}}, {"status": "A"}, True),
    ({"note": {"contains": "trip"}}, {"note": "Compressor TRIP at 03:10"}, True),
    ({"note": {"present": True}}, {"note": ""}, False),
    ({"note": {"absent": True}}, {}, True),
    ({"flow_hours": {"ge": 20, "lt": 23}, "status": "A"}, {"flow_hours": 22, "status": "A"}, True),
    ({"flow_hours": {"ge": 20, "lt": 23}, "status": "A"}, {"flow_hours": 19, "status": "A"}, False),
    ({"count": 3}, {"count": "3.0"}, True),
])
def test_conditions(when, payload, expected):
    assert workflows.meets(workflows._check_when(when), payload) is expected


def test_templates_fill_columns_and_leave_missing_ones_empty():
    assert workflows.fill("{meter}:{gas_day}", {"meter": "H1FT8010", "gas_day": "2026-11-09"}) == "H1FT8010:2026-11-09"
    assert workflows.fill("x {missing}", {}) == "x "
    assert workflows.fill("{a.__class__}", {"a": 1}) == ""  # a column name, never an attribute


def test_refs_from_templates_skip_empty_values():
    assert workflows._refs(["sap/{id}", "tag:{tag}"], {"id": "1004", "tag": None}, None) == ["sap/1004"]


def test_bad_conditions_are_refused():
    with pytest.raises(CatalogError, match="unknown condition"):
        workflows._check_when({"x": {"between": [1, 2]}})
    with pytest.raises(CatalogError, match="take a list"):
        workflows._check_when({"x": {"in": "a"}})
    with pytest.raises(CatalogError, match="resolver"):
        workflows._check_refs([{"from": "meter", "as": "nope"}])


def test_a_webhook_secret_is_a_reference_never_the_value():
    workflows.check_secret_ref("env:KESTREL_HOOK")
    workflows.check_secret_ref(None)
    with pytest.raises(CatalogError, match="reference"):
        workflows.check_secret_ref("hunter2")
