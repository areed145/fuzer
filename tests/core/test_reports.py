"""Report definitions (docs/reports.md): checked when read, so a definition that can't be drawn is refused with why."""

import pytest

from fuzer.core import reports

pytestmark = pytest.mark.unit


def _d(*panels, **extra):
    return {"key": "t", "title": "T", "scope": {"kinds": ["facility"]}, "rows": [{"panels": list(panels)}], **extra}


def test_the_platform_reports_are_checked_with_defaults_filled():
    d = reports.builtin()["work"]
    assert d["schema"] == 1 and d["source"] == "platform" and d["window"]["default"] == "7d"
    ids = [p["id"] for p in reports.panels(d)]
    assert len(ids) == len(set(ids)) and all(r["id"] for r in d["rows"])
    stat = next(p for p in reports.panels(d) if p["type"] == "stat")
    assert stat["size"] == 1 and stat["row"] == "headline"


@pytest.mark.parametrize("bad, why", [
    (_d({"id": "a", "type": "gauge"}), "panel type 'gauge'"),
    (_d({"id": "a", "type": "stat", "measure": "no.such"}), "no measure 'no.such'"),
    (_d({"id": "a", "type": "figure", "measure": "work.backlog", "grain": "well"}), "can't be cut by 'well'"),
    (_d({"id": "a", "type": "stat", "measure": "work.backlog", "size": 5}), "size is 1 to 4"),
    (_d({"id": "a", "type": "stat", "measure": "work.backlog"}, {"id": "a", "type": "stat", "measure": "work.closed"}),
     "used twice"),
    (_d({"id": "a", "type": "cases", "filter": {"colour": "red"}}), "cases filter takes"),
    (_d({"id": "a", "type": "table", "rows": "no.such"}), "no row set"),
    (_d({"id": "a", "type": "stat", "measure": "work.backlog"}, scope={"kinds": ["planet"]}), "aren't kinds"),
    (_d({"id": "a", "type": "stat", "measure": "work.backlog"}, window={"default": "2w"}), "no window '2w'"),
    ({**_d(), "rows": [{"panels": [{"type": "column", "panels": [{"type": "column", "panels": []}]}]}]}, "one level"),
])
def test_a_definition_that_cant_be_drawn_is_refused_with_why(bad, why):
    with pytest.raises(reports.ReportError, match=why):
        reports.check(bad)


def test_a_pack_adds_and_replaces_reports():
    from types import SimpleNamespace

    own = reports.checked({"work": _d({"id": "a", "type": "stat", "measure": "work.backlog"}, title="Ours"),
                           "mine": _d({"id": "b", "type": "summary"})})
    got = reports.load(SimpleNamespace(reports=own))
    assert got["work"]["title"] == "Ours" and got["work"]["source"] == "bundle" and "mine" in got
    with pytest.raises(reports.ReportError, match="no report"):
        reports.get(None, "mine")
