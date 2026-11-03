"""Coupled synthetic process values (#91): on the Okan demo, process values come from a small model of
each unit, so they move together as the plant would and causes lead their effects. Values stay
deterministic and the same from any window, the fake historian serves the same numbers, and points
outside the model keep the per-point signals."""

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from fakeplant import historian, okan
from fuzer import pipeline, process_model, queries
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.sources import ingest, timeseries

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def data():
    out = Path(tempfile.mkdtemp(prefix="fzokan"))
    okan.generate(out)
    return out


@pytest.fixture(scope="module")
def model(data):
    return process_model.load(data)


def point(data, unit, var):
    return next(p for p, uv in json.loads((data / "process_model.json").read_text())["points"].items() if uv == [unit, var])


def arrays(model, start, end, *uvs):
    w = model.window(datetime.fromisoformat(start), datetime.fromisoformat(end))
    keep = (w.t >= process_model.minutes(start)) & (w.t <= process_model.minutes(end))
    return [w.get(u, v)[keep] for u, v in uvs]


def at(model, data, unit, var, when):
    t = datetime.fromisoformat(when)
    return model.values(point(data, unit, var), t, t, 5)[0][1]


def test_points_are_modelled_and_values_are_window_independent(data, model):
    spec = json.loads((data / "process_model.json").read_text())
    assert len(spec["points"]) > 900  # equipment, headers, meters, jackets, wells
    p = point(data, "header:20", "pressure")
    a = dict(model.values(p, datetime(2024, 3, 14, 2), datetime(2024, 3, 14, 6), 10))
    b = dict(model.values(p, datetime(2024, 3, 1), datetime(2024, 3, 31), 10))
    c = dict(model.values(p, datetime(2024, 3, 14), datetime(2024, 3, 15), 5))
    assert all(abs(b[k] - v) < 1e-6 and abs(c[k] - v) < 1e-6 for k, v in a.items())
    # the fake PI / ADX historian serves the same numbers
    plant = historian.Plant(data)
    got = plant.values(p, datetime(2024, 3, 14, 2), datetime(2024, 3, 14, 6), 10)
    assert [round(v, 3) for _, v in got] == [v for _, v in sorted(a.items())]


def test_compressor_trip_drops_the_lift_gas_header_and_the_wells_follow(data, model):
    before = at(model, data, "header:20", "pressure", "2024-03-14T02:00")
    during = at(model, data, "header:20", "pressure", "2024-03-14T05:00")
    assert before > 85 and 55 < during < 66
    glir = [at(model, data, "OK-J07-05", "glir", t) for t in ("2024-03-14T02:00", "2024-03-14T05:00")]
    assert glir[1] < 0.75 * glir[0]
    assert at(model, data, "valve:20-XV-3100", "position", "2024-03-14T05:00") > 50  # bypass opened


def test_vibration_leads_temperature_before_the_trip(model):
    vib, temp = arrays(model, "2024-03-13T21:00", "2024-03-14T03:15", ("20-K-2200B", "vibration"),
                       ("20-K-2200B", "discharge_temperature"))
    assert vib[-1] > 10 and temp[-1] > temp[0] + 3

    def corr(lag):
        return float(np.corrcoef(vib[:len(vib) - lag] if lag else vib, temp[lag:])[0, 1])

    best = max(range(0, 18), key=corr)  # 5-minute steps
    assert best * 5 >= 15  # temperature follows vibration by tens of minutes


def test_pump_discharge_pressure_falls_with_flow(model):
    q, p, on = arrays(model, "2024-03-13T00:00", "2024-03-17T00:00", ("10-P-1001A", "flow"),
                      ("10-P-1001A", "discharge_pressure"), ("10-P-1001A", "running"))
    running = on > 0.999  # not starting or stopping
    assert running.mean() > 0.5 and float(np.corrcoef(q[running], p[running])[0, 1]) < -0.8


def test_gas_lift_valve_failed_open(model):
    (g0, c0), (g1, c1) = [arrays(model, a, b, ("OK-J07-03", "glir"), ("OK-J07-03", "casp"))
                          for a, b in (("2024-07-01T00:00", "2024-08-01T00:00"), ("2024-09-15T00:00", "2024-10-15T00:00"))]
    assert g1.mean() > 1.3 * g0.mean() and c1.mean() < c0.mean() - 5


def test_the_other_stories(data, model):
    # train A 3rd stage runs hot into its trips; the inlet separator carries liquid into train C
    assert at(model, data, "20-K-2300A", "discharge_temperature", "2024-06-11T14:55") > 160
    assert at(model, data, "20-K-2300A", "discharge_temperature", "2024-06-11T10:00") < 155
    lvl, = arrays(model, "2024-11-05T02:00", "2024-11-05T05:30", ("20-V-2101C", "liquid_level"))
    assert lvl.max() > 80 and lvl[0] < 50
    hunting, = arrays(model, "2024-11-06T00:00", "2024-11-07T00:00", ("20-V-2001", "liquid_level"))
    calm, = arrays(model, "2024-10-06T00:00", "2024-10-07T00:00", ("20-V-2001", "liquid_level"))
    assert hunting.std() > 3 * calm.std()
    # hydrate on OK-NWP-G2: wellhead temperature falls and pressure rises before it's shut in
    wht, whp = arrays(model, "2025-01-13T23:00", "2025-01-14T05:00", ("OK-NWP-G2", "wht"), ("OK-NWP-G2", "whp"))
    assert wht[-1] < wht[0] - 8 and whp[-1] > whp[0] + 10
    # the export pump seal leak: B stops, C takes over
    assert at(model, data, "10-P-1001B", "discharge_pressure", "2025-05-20T12:00") < 5
    assert at(model, data, "10-P-1001C", "discharge_pressure", "2025-05-20T12:00") > 30


def test_reading_a_month_of_twelve_points_is_quick(data, model):
    pts = list(json.loads((data / "process_model.json").read_text())["points"])[:12]
    started = time.perf_counter()
    for p in pts:
        model.values(p, datetime(2025, 3, 1), datetime(2025, 3, 31), 240)
    assert time.perf_counter() - started < 0.2


def test_provider_uses_the_model_and_falls_back(data, monkeypatch):
    monkeypatch.setenv("FUZER_DATA_DIR", str(data))
    src = timeseries.SyntheticProvider()
    p = point(data, "header:20", "pressure")
    vals = src.values(p, "barg", datetime(2024, 3, 14, 5), datetime(2024, 3, 14, 5), 5, [])
    assert vals[0][1] < 66
    plain = src.values("NOT-IN-MODEL.PV", "barg", datetime(2024, 3, 14, 5), datetime(2024, 3, 14, 5), 5, [])
    assert plain == timeseries.SyntheticProvider(data_dir=tempfile.mkdtemp()).values(
        "NOT-IN-MODEL.PV", "barg", datetime(2024, 3, 14, 5), datetime(2024, 3, 14, 5), 5, [])


def test_charts_on_modelled_points(data, monkeypatch):
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    monkeypatch.setenv("FUZER_DATA_DIR", str(data))
    from fuzer.plots import troubleshooting

    with db.connect() as conn:
        db.reset(conn)
        ingest.ingest(conn, data)
        pipeline.rebuild(conn, PACK)
        k = queries.resolve_ref(conn, PACK, "20-K-2200B")
        tt = conn.execute("select dst from edge where rel = 'has_timeseries' and src = %s",
                          (queries.resolve_ref(conn, PACK, "20-TT-2200B"),)).fetchone()["dst"]
        cor = troubleshooting.correlation(conn, k, at="2024-03-14T03:00", hours_before=8, hours_after=0,
                                          upstream=False, target=tt)
        assert "process model" in cor.summary["note"]
        vib = next(r for r in cor.summary["against_target"]["best_lag"] if "VT" in r["point"])
        assert vib["leads_by_minutes"] > 0 and vib["r"] > 0.5
        out = timeseries.around(conn, k, "2024-03-14T03:00", 6, 1)
        assert all(s.get("modelled") for s in out["series"])
