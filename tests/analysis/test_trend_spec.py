"""Trend specs: the one config every chart over time draws. Panels name series by key, every series is worded once
on the server, and `focus` opens the chart on the series a page is about."""

import pytest

from fuzer.analysis import trend_spec

VALS = [["2024-07-24T10:00", 1.0], ["2024-07-24T11:00", 2.0]]


def _trace(tid, uom="psig", **extra):
    return {"id": tid, "point": f"SRV:{tid}.PV", "description": None, "uom": uom, "axis": uom.lower(), "show": True,
            "values": VALS, "min": 1.0, "max": 2.0, "mean": 1.5, "last": 2.0, "role": None, "role_label": None,
            "on": {"id": "u", "label": "K-1", "relation": "self"}, **extra}


def _view(**extra):
    return {
        "entity": "fz-k1", "unit": "u", "unit_label": "K-1", "provider": "synthetic", "step_minutes": 10,
        "start": "2024-07-24T00:00", "end": "2024-07-25T00:00", "centre": "2024-07-24T16:00",
        "upsets": ["2024-07-24T13:45", "2024-07-20T00:00"],
        "template": {"key": "CO", "title": "Compressor", "source": "platform"},
        "groups": [
            {"key": "pressures", "label": "Pressures", "show": True, "traces": [
                _trace("suction", role="suction_pressure", role_label="Suction pressure"),
                _trace("discharge", role="discharge_pressure", role_label="Discharge pressure",
                       limits=[{"type": "hi", "value": 3, "basis": "alarm"}])]},
            {"key": "vibration", "label": "Vibration", "show": True, "traces": [
                _trace("tb", "degF", role="bearing_temperature", role_label="Bearing temperature",
                       description="Train B Compressor Thrust Bearing Temperature"),
                _trace("jb", "degF", role="bearing_temperature", role_label="Bearing temperature",
                       description="Train B Compressor Journal Bearing Temperature"),
                _trace("speed", "rpm", role="speed", role_label="Speed", on={"id": "g", "label": "GT-1", "relation": "driver"})]},
            {"key": "tests", "label": "Tests and samples", "show": False, "traces": [
                _trace("m:u|oil|", "bbl/d", sampled=True, methods=["test"], show=False, link="u")]},
        ],
        "other": [_trace("spare", "degC", show=False, description="Spare Inlet Temperature")],
        "missing": [], **extra,
    }


@pytest.mark.unit
def test_panels_name_their_series_by_key_and_every_series_is_worded_once():
    s = trend_spec.from_view(_view())
    assert s["version"] == 1 and s["title"] == "K-1 · Compressor"
    assert [p["key"] for p in s["panels"]] == ["pressures", "vibration", "tests", "other"]
    assert s["panels"][0]["series"] == ["suction", "discharge"] and s["panels"][-1]["show"] is False
    by = {x["key"]: x for x in s["series"]}
    assert set(by) == {k for p in s["panels"] for k in p["series"]}
    # twins read by what tells them apart; a neighbour's series names the neighbour
    assert by["tb"]["label"] == "Thrust bearing temperature" and by["jb"]["label"] == "Journal bearing temperature"
    assert by["speed"]["label"] == "Speed · GT-1"
    assert by["spare"]["label"] == "Spare inlet temperature"
    # units printed here, the tag without its server and suffix, limits carried
    assert by["tb"]["unit"] == "°F" and by["spare"]["unit"] == "°C" and by["suction"]["unit"] == "psig"
    assert by["suction"]["tag"] == "suction" and by["discharge"]["limits"][0]["value"] == 3
    # a landed reading draws as steps and opens the record it is on
    assert by["m:u|oil|"]["as"] == "step" and by["m:u|oil|"]["link"] == "u" and by["m:u|oil|"]["methods"] == ["test"]
    assert by["suction"]["as"] == "line" and by["suction"]["show"] and not by["spare"]["show"]


@pytest.mark.unit
def test_the_lead_into_an_incident_in_the_window_is_a_mark_and_every_lane_is_listed():
    s = trend_spec.from_view(_view())
    assert s["marks"] == [{"kind": "window", "start": "2024-07-24T01:45", "end": "2024-07-24T13:45",
                           "label": "12 h before the incident", "tone": "warn"}]
    assert [e["lane"] for e in s["events"]] == ["alarms", "log", "cases", "maintenance", "downtime"]
    assert s["time"] == {"start": "2024-07-24T00:00", "end": "2024-07-25T00:00", "centre": "2024-07-24T16:00"}


@pytest.mark.unit
def test_focus_opens_on_the_series_named_by_key_point_tag_or_role():
    assert trend_spec.from_view(_view(), ["discharge_pressure"])["focus"] == ["discharge"]
    assert trend_spec.from_view(_view(), ["SRV:tb.PV", "speed"])["focus"] == ["tb", "speed"]
    assert trend_spec.from_view(_view(), ["bearing_temperature"])["focus"] == ["tb", "jb"]
    assert trend_spec.from_view(_view(), ["nothing here"])["focus"] is None
    assert trend_spec.from_view(_view())["focus"] is None


@pytest.mark.unit
def test_an_error_stays_an_error():
    assert trend_spec.from_view({"error": "unknown entity"}) == {"version": 1, "error": "unknown entity"}


@pytest.mark.unit
def test_names_match_the_browser_rules():
    assert trend_spec.sentence("Thrust Bearing Temperature") == "Thrust bearing temperature"
    assert trend_spec.sentence("GT-1 Exhaust TC4") == "GT-1 exhaust TC4"
    assert trend_spec.tag("OK_GGCP:20-FT-2100B.PV") == "20-FT-2100B"
    four = [f"Exhaust Thermocouple {i}" for i in range(1, 5)]
    assert trend_spec._distinct(four[3], four) == "Exhaust Thermocouple 4"


def test_a_compressor_spec_reads_its_template(okan_db):
    conn = okan_db.conn
    unit = conn.execute(
        """
        select c.src as id from edge c join entity_label l on l.fuzer_id = c.dst
        join edge d on d.dst = c.src and d.rel = 'drives'
        where c.rel = 'classified_as' and l.label like 'ISO14224:CO%%' order by 1 limit 1
        """).fetchone()["id"]
    s = trend_spec.spec(conn, unit, focus=["discharge_pressure"])
    assert s["template"]["key"] == "CO" and s["subject"] == unit
    assert {"pressures", "temperatures", "driver"} <= {p["key"] for p in s["panels"]}
    by = {x["key"]: x for x in s["series"]}
    assert s["focus"] and all(by[k]["role"] == "discharge_pressure" for k in s["focus"])
    assert all(x["values"] and x["label"] and x["axis"] for x in s["series"])
    assert len(by) == len(s["series"])


@pytest.mark.unit
def test_a_pages_own_marks_are_drawn_beside_the_trends():
    case = {"kind": "time", "at": "2024-07-24T13:20", "label": "Step change", "tone": "danger"}
    s = trend_spec.from_view(_view(), marks=[case])
    assert s["marks"][-1] == case and s["marks"][0]["label"] == "12 h before the incident"


def test_an_incident_spec_reads_its_units_trend_with_the_lead_in_marked(okan_db):
    conn = okan_db.conn
    row = conn.execute(
        "select a.src as id from edge a join entity i on i.fuzer_id = a.src and i.kind = 'incident' "
        "where a.rel = 'about' order by 1 limit 1").fetchone()
    s = trend_spec.spec(conn, row["id"])
    assert s["series"] and s["panels"]
    assert any(m["label"] == "12 h before the incident" for m in s["marks"])


@pytest.mark.unit
def test_landed_readings_are_a_panel_each_over_their_whole_history():
    data = {"id": "w1", "truncated": False, "series": [
        {"quantity": "wall_thickness", "location": "CML-3", "of": {"id": "p9", "label": "Line 9"}, "unit": "in", "count": 2,
         "first": "2023-01-05T00:00:00", "min": 0.48, "max": 0.5, "per_year": -0.012,
         "latest": {"at": "2025-06-01T00:00:00", "value": 0.48, "qualifier": None, "method": "UT", "record": "r2"},
         "values": [{"at": "2023-01-05T00:00:00", "value": 0.5, "qualifier": None, "reported": None, "method": "UT", "record": "r1"},
                    {"at": "2025-06-01T00:00:00", "value": 0.48, "qualifier": None, "reported": None, "method": "UT", "record": "r2"}]},
        {"quantity": "h2s", "location": None, "of": {"id": "w1", "label": "W-1"}, "unit": "ppm", "count": 1,
         "first": "2024-02-01T00:00:00", "min": 0.5, "max": 0.5, "per_year": None,
         "latest": {"at": "2024-02-01T00:00:00", "value": 0.5, "qualifier": "<", "method": "lab", "record": "r3"},
         "values": [{"at": "2024-02-01T00:00:00", "value": 0.5, "qualifier": "<", "reported": "<0.5", "method": "lab", "record": "r3"}]},
    ]}
    s = trend_spec.from_measurements(data)
    assert [p["label"] for p in s["panels"]] == ["Wall thickness at CML-3 · Line 9", "H2s"]
    wall, h2s = s["series"]
    assert wall["label"] == "Wall thickness at CML-3 · Line 9 · -0.012 in/year" and wall["as"] == "points"
    assert wall["values"] == [["2023-01-05T00:00", 0.5], ["2025-06-01T00:00", 0.48]] and wall["link"] == "p9"
    assert h2s["methods"] == ["< below the detection limit · lab"]
    assert s["time"] == {"start": "2023-01-05T00:00", "end": "2025-06-01T00:00", "centre": "2025-06-01T00:00"}
    assert trend_spec.from_measurements({"id": "x", "series": []})["error"] == "no readings"


def test_a_trend_compares_with_last_week_and_the_units_past_incidents_lined_up_on_its_window(okan_db):
    conn = okan_db.conn
    row = conn.execute(
        "select a.dst as unit, count(*) from edge a join entity i on i.fuzer_id = a.src and i.kind = 'incident' "
        "where a.rel = 'about' group by a.dst order by count(*) desc, a.dst limit 1").fetchone()
    week = trend_spec.spec(conn, row["unit"], compare="7d")
    assert week["compare"] == {"mode": "7d", "labels": ["7 d earlier"]}
    shown = [x for x in week["series"] if x["show"]]
    assert shown and all(x["compare"][0]["label"] == "7 d earlier" for x in shown)
    x = shown[0]
    # moved forward a week: the earlier window's times land on this window's
    assert x["compare"][0]["values"][0][0][:13] == x["values"][0][0][:13]
    past = trend_spec.spec(conn, row["unit"], compare="incidents")
    assert len(past["compare"]["labels"]) <= trend_spec.PAST_INCIDENTS
    assert all(label.endswith("incident") for label in past["compare"]["labels"])
    assert "compare" not in trend_spec.spec(conn, row["unit"], compare="sideways")
