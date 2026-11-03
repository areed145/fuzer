"""Operating limits (#101): PI AF limit traits, DCS alarms and an IOW register land against PI points,
dated and with their source; checks find the limits planted wrong or missing; units' run status says
when limits count; excursions are recorded while units run, and an IOW breach past its allowed time
opens an integrity work item."""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fakeplant import okan
from fuzer import mcp_server
from fuzer.analysis import limits
from fuzer.core import db, units
from fuzer.core.pack import load_pack
from fuzer.graph import pipeline, queries
from fuzer.sources import ingest, mappings, timeseries
from fuzer.workflow import engine

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def data():
    out = Path(tempfile.mkdtemp(prefix="fzokan"))
    okan.generate(out)
    return out


@pytest.fixture(scope="module")
def conn(data):
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    old = os.environ.get("FUZER_DATA_DIR")
    os.environ["FUZER_DATA_DIR"] = str(data)  # the synthetic provider reads the plant's process model
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        c.stats = pipeline.rebuild(c, PACK)
        engine.load_defaults(c)
        limits.learn(c, PACK, "2025-01-01", "2025-03-31")  # a quiet quarter: no stories running
        yield c
    if old is None:
        os.environ.pop("FUZER_DATA_DIR", None)
    else:
        os.environ["FUZER_DATA_DIR"] = old


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def point(conn, name):
    return conn.execute("select fuzer_id from entity_label where label = %s", (name,)).fetchone()["fuzer_id"]


def test_durations_as_registers_write_them():
    assert [units.minutes(x) for x in ("2 h", "4 hrs", "24 hours", "120 min", "1 day", "3", "", None, "n/a")] == [
        120, 240, 1440, 120, 1440, 180, None, None, None]


def test_mappings_read_af_traits_dcs_alarms_and_iows():
    m = mappings.load()
    dcs = m[("dcs", "alarm")].limits("30PT2001>PVHI", {
        "tag": "30PT2001", "alarm": "PVHI", "setpoint": "200", "eu": "psig", "priority": "2", "enabled": "N",
        "last_changed": "2019-05-14"}, PACK)
    assert [(x.limit_type, round(x.value, 2), x.unit, x.reported, x.enabled, x.level) for x in dcs] == [
        ("hi", 13.79, "barg", "200 psig", False, "2")]
    assert dcs[0].of == [("timeseries", "30PT2001"), ("tag", "30-PT-2001")]

    iow = m[("iow", "window")].limits("IOW-001>B", {
        "iow_id": "IOW-001", "revision": "B", "tag": "20-TT-2300A", "parameter": "discharge temperature",
        "iow_class": "Critical", "low_limit": "", "high_limit": "144", "unit": "degC", "max_time_outside": "2 h",
        "damage_mechanism": "seal damage", "response": "cut load", "effective_date": "2025-01-01",
        "superseded_date": ""}, PACK)
    assert [(x.limit_type, x.value, x.basis, x.level, x.allowed_minutes, x.valid_from, x.valid_to) for x in iow] == [
        ("hi", 144.0, "iow", "critical", 120.0, "2025-01-01", None)]
    assert iow[0].note.startswith("IOW-001 rev B")

    af = m[("pi_af", "element")].limits("OKAN\\10 OKA_PP\\10-V-2001", {"attributes": [
        {"name": "Pressure", "pi_point": "oka_pp.10pt2001.pv", "uom": "barg", "Hi": 14, "HiHi": 15.5},
        {"name": "Level", "pi_point": "OKA_PP.10LT2001.PV", "uom": "%"}]}, PACK)
    assert [(x.record, x.limit_type, x.value) for x in af] == [
        ("pi_af:element:OKAN\\10 OKA_PP\\10-V-2001#1", "hihi", 15.5), ("pi_af:element:OKAN\\10 OKA_PP\\10-V-2001#1", "hi", 14.0)]
    assert af[0].of == [("timeseries", "OKA_PP.10PT2001.PV")]


def test_limits_land_dated_against_points(conn):
    assert conn.stats["limits"] > 1500
    assert conn.stats["unresolved_limits"] == {"iow window: unknown tag": 1}  # IOW-010's typo
    sources = {r["source"]: r["n"] for r in conn.execute("select source, count(*) as n from point_limit group by 1")}
    assert set(sources) == {"pi_af", "dcs", "iow"} and sources["dcs"] > sources["pi_af"]
    # ST1's DCS is in psig; stored in barg with what it said kept.
    st1 = conn.execute("select value, unit, reported from point_limit where source = 'dcs' and reported like '%%psig' "
                       "limit 1").fetchone()
    assert st1["unit"] == "barg" and st1["value"] < 100
    # The IOW on an instrument tag counts for its one point; rev A was superseded by rev B.
    rows = conn.execute("select record, value, valid_from, valid_to, level, allowed_minutes from point_limit "
                        "where point_id = %s and basis = 'iow' order by record", (point(conn, "OK_GGCP:20-TT-2300A.PV"),)).fetchall()
    assert [(r["record"], r["valid_to"]) for r in rows] == [
        ("iow:window:IOW-001>A", datetime(2025, 1, 1)), ("iow:window:IOW-001>B", None)]
    assert rows[1]["level"] == "critical" and rows[1]["allowed_minutes"] == 120 and rows[0]["value"] > rows[1]["value"]


def test_which_limits_count(conn):
    temp = point(conn, "OK_GGCP:20-TT-2300A.PV")
    before = limits.for_points(conn, PACK, [temp], datetime(2024, 6, 1))[temp]
    after = limits.for_points(conn, PACK, [temp], datetime(2025, 6, 1))[temp]
    assert before["iow"][0]["hi"] > after["iow"][0]["hi"] and after["iow"][0]["iow"] == "IOW-001"
    assert before["alarm"]["hi"]["source"] == "dcs" and set(before["alarm"]) == {"hi", "hihi"}
    # A disabled alarm doesn't count; AF's value comes through instead.
    truth = json.loads((Path(os.environ["FUZER_DATA_DIR"]) / "answer_keys" / "limits.json").read_text())
    off = point(conn, next(x["point"] for x in truth if x["fault"] == "disabled"))
    got = limits.for_points(conn, PACK, [off])[off]["alarm"]
    assert got.get("hi", {}).get("source") in (None, "pi_af")
    # A point without source limits gets its learned range, labelled learned.
    wht = limits.for_points(conn, PACK, [point(conn, "OK_J07_03.WHT")])
    lr = next(iter(wht.values()))["learned"]
    assert lr["lo"] < lr["hi"] and lr["mode"] == "running"


def test_checks_find_what_was_planted(conn, data):
    truth = json.loads((data / "answer_keys" / "limits.json").read_text())
    found = limits.issues(conn, PACK)
    by_point: dict[str, set] = {}
    for i in found:
        by_point.setdefault(i["point"], set()).add(i["fault"])
    expect = {"wrong_unit": "implausible"}
    for x in truth:
        if x["fault"] == "unresolved":
            continue
        assert expect.get(x["fault"], x["fault"]) in by_point.get(x["point"], set()), x
    planted = {x["point"] for x in truth}
    assert set(by_point) <= planted, set(by_point) - planted  # nothing flagged that wasn't planted


def test_units_run_status(conn):
    pumps = {t: fid(conn, t) for t in ("10-P-1001A", "10-P-1001B", "10-P-1001C")}
    a, b = datetime(2025, 5, 19), datetime(2025, 5, 25)
    m = limits.modes(conn, PACK, set(pumps.values()), a, b, 30)
    t = [a + (b - a) * i / (len(m[pumps["10-P-1001B"]]) - 1) for i in range(len(m[pumps["10-P-1001B"]]))]
    at = lambda when: t.index(datetime.fromisoformat(when))  # noqa: E731
    assert m[pumps["10-P-1001B"]][at("2025-05-21T12:00")] == "stopped"  # seal leak, isolated
    assert m[pumps["10-P-1001C"]][at("2025-05-21T12:00")] == "running"  # the standby covering for it
    assert m[pumps["10-P-1001C"]][at("2025-05-19T12:00")] == "stopped"
    assert m[pumps["10-P-1001B"]][at("2025-05-23T13:00")] == "startup"  # back, settling
    # A vessel on a compressor train follows its train: train C's scrubber stops with the trip.
    scrubber = fid(conn, "20-V-2101C")
    trip = limits.modes(conn, PACK, {scrubber}, datetime(2024, 11, 5, 4), datetime(2024, 11, 5, 12), 30)[scrubber]
    assert trip[0] == "running" and "stopped" in set(trip)


def test_find_holds_through_the_deadband():
    times = [datetime(2025, 1, 1, h) for h in range(10)]
    v = np.array([1.0, 2.1, 1.995, 2.2, 1.5, 1.5, 1.5, 1.5, 2.3, 2.3])
    run = np.array([True] * 7 + [False, True, True])
    got = limits.find(times, v, run, {"limit_type": "hi", "value": 2.0}, 60)
    # 1.995 is inside by less than the deadband, so 01:00-04:00 is one stretch; stopped at 07:00, so the
    # 08:00 crossing is separate and still open at the end.
    assert [(x["start"].hour, x["end"] and x["end"].hour, x["peak"], x["minutes_outside"]) for x in got] == [
        (1, 4, 2.2, 180.0), (8, None, 2.3, 120.0)]


def test_stopped_units_raise_no_excursions(conn):
    pts = [point(conn, p) for p in ("OKA_PP.10FT1001B.PV", "OKA_PP.10JT1001B.PV", "OKA_PP.10PT1001B.PV",
                                     "OKA_PP.10FT1001C.PV", "OKA_PP.10JT1001C.PV")]
    got = limits.excursions(conn, PACK, pts, datetime(2025, 5, 19), datetime(2025, 5, 25), 10)
    # Pump B is down for three days and C stands by the rest of the week: their low flow, power and
    # pressure never count. What does: on the 23rd A is swapped out for maintenance while B is still
    # isolated, so C carries the whole export flow alone, far above its high flow and power limits.
    assert not [x for x in got if x["limit_type"] in ("lo", "lolo")]
    assert {(x["point"], x["start"]) for x in got} == {
        ("OKA_PP.10FT1001C.PV", datetime(2025, 5, 23, 6)), ("OKA_PP.10JT1001C.PV", datetime(2025, 5, 23, 6))}
    # Without the run status they would.
    every = limits.find([datetime(2025, 5, 21)], np.array([0.0]), np.array([True]), {"limit_type": "lo", "value": 50}, 10)
    assert len(every) == 1


def test_story_excursions(conn):
    level = point(conn, "OK_GGCP:20-LT-2001.PV")
    got = limits.excursions(conn, PACK, [level], datetime(2024, 11, 1), datetime(2024, 11, 14), 10)
    kinds = {(x["basis"], x["limit_type"]) for x in got}
    # The inlet separator runs high twice before train C trips, and its transmitter hunts.
    assert {("alarm", "hi"), ("alarm", "hihi")} <= kinds
    assert all(x["start"] >= datetime(2024, 11, 4) for x in got)


def test_iow_breach_past_its_time_opens_work(conn):
    temp = point(conn, "OK_GGCP:20-TT-2300A.PV")
    out = limits.scan(conn, PACK, "2025-07-10", "2025-07-20", point_ids=[temp])
    assert out["by_basis"]["iow"] >= 3
    [work] = [w for w in out["work"] if w["iow"] == "IOW-001"]
    assert work["started"] and "critical" in work["title"]
    item = conn.execute("select * from work_item where id = %s", (work["item_id"],)).fetchone()
    assert item["type"] == "integrity" and item["state"] == "open"
    proc = conn.execute("select data, correlation_key from work_process where item_id = %s", (item["id"],)).fetchone()
    assert proc["correlation_key"] == "IOW-001" and proc["data"]["breaches"] >= 3
    step = conn.execute("select element, role, requires_human from work_token where item_id = %s and state = 'waiting'",
                        (item["id"],)).fetchone()
    assert (step["element"], step["role"], step["requires_human"]) == ("review", "engineer", True)
    linked = conn.execute("select count(*) as n from excursion where work_item_id = %s", (item["id"],)).fetchone()["n"]
    assert linked == len(work["excursions"])

    # The next scan's breaches go to the same item as evidence; the excursion open at the end is extended.
    again = limits.scan(conn, PACK, "2025-07-20", "2025-07-25", point_ids=[temp])
    assert [(w["item_id"], w["started"]) for w in again["work"] if w["iow"] == "IOW-001"] == [(item["id"], False)]
    assert conn.execute("select count(*) as n from work_item where type = 'integrity'").fetchone()["n"] == 1

    # Informational IOWs are recorded, never worked.
    assert all(w["iow"] not in ("IOW-008", "IOW-009") for w in out["work"] + again["work"])


def test_trends_api_and_mcp_carry_limits(conn):
    temp = point(conn, "OK_GGCP:20-TT-2300A.PV")
    tv = timeseries.around(conn, temp, "2025-07-16T15:00", 24, 6, pack=PACK)
    lines = tv["series"][0]["limits"]
    assert {(x["basis"], x["type"]) for x in lines} >= {("alarm", "hi"), ("alarm", "hihi"), ("iow", "hi")}

    from fuzer import api

    client = TestClient(api.app)
    comp = fid(conn, "20-K-2300A")
    r = client.get(f"/entities/{comp}/limits").json()
    by = {p["point"]: p for p in r["points"]}
    assert by["OK_GGCP:20-TT-2300A.PV"]["iow"][0]["level"] == "critical"
    assert by["OK_GGCP:20-TT-2300A.PV"]["excursions"]  # recorded by the scan above
    assert client.get("/limits/issues").status_code == 200
    assert client.get("/excursions", params={"basis": "iow"}).json()[0]["basis"] == "iow"

    got = mcp_server.limits("20-TT-2300A")
    assert got["points"][0]["alarm"]["hi"]["source"] == "dcs" and got["points"][0]["sources"]
