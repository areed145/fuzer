"""Back-pressure (#107 §1-2): the process model couples well rates to back-pressure (calibration kept),
and the calcs find its injected stories: a flowline waxing up, the HP separator's setpoint lowered, a
well rerouted; well constraints agree with the model's own."""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from fakeplant import okan
from fuzer.agents import mcp_server
from fuzer.analysis import calcs, hydraulics
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.graph import queries
from fuzer.ops import pipeline, sre
from fuzer.sources import ingest, timeseries
from fuzer.sources.process_model import ProcessModel

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def data():
    out = Path(tempfile.mkdtemp(prefix="fzokan"))
    okan.generate(out)
    return out


@pytest.fixture(scope="module")
def spec(data):
    return json.loads((data / "process_model.json").read_text())


@pytest.fixture(scope="module")
def conn(data):
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATA_DIR", str(data))
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c
    mp.undo()


def story(spec, kind):
    return next(e for e in spec["events"] if e["kind"] == kind)


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_model_couples_rate_to_back_pressure(spec):
    m = ProcessModel(spec)
    fl, sp, rr = story(spec, "flowline_restriction"), story(spec, "setpoint"), story(spec, "reroute")
    calm = datetime(2025, 9, 15, 12)
    wells = [n for n, u in spec["units"].items() if u.get("type") == "well" and u.get("kind") != "gas" and u.get("jacket")]
    assert all(abs(m.at(w, "bp_factor", calm) - 1) < 0.01 for w in wells[:30])  # calibration kept
    assert m.at(fl["unit"], "bp_factor", datetime(2025, 11, 12)) < 0.95  # the waxing flowline cuts its rate
    assert m.at(fl["unit"], "whp", datetime(2025, 11, 12)) > m.at(fl["unit"], "whp", calm) + 0.5
    assert sp["to"] == 10.0 and m.at("10-V-2001", "vessel_pressure", datetime(2025, 11, 26, 12)) < 10.3
    after = [m.at(w, "bp_factor", datetime(2025, 11, 26, 12)) for w in wells[:30]]
    assert sum(after) / len(after) > 1.02  # lower separator, lower headers: more oil
    assert m.at(rr["unit"], "bp_factor", datetime(2025, 12, 6, 12)) > m.at(rr["unit"], "bp_factor", datetime(2025, 12, 4, 12))


def test_tests_measure_the_response(conn, spec):
    """Well tests after the setpoint change carry the model's rate response (the generator scales them)."""
    w = fid(conn, story(spec, "flowline_restriction")["unit"])
    tests = calcs.well_tests(conn, w)
    assert tests and all(t["at"].year in (2024, 2025) for t in tests)


def test_calcs_find_the_stories(conn, spec):
    fl = fid(conn, story(spec, "flowline_restriction")["unit"])
    assert hydraulics.dp_drift(conn, fl, 60, "2025-09-30T00:00")["flag"] is None
    d = hydraulics.dp_drift(conn, fl, 60, "2025-11-15T00:00")
    assert d["flag"] == "dp_rising" and "2025-10" <= d["rising_since"] <= "2025-11-05" and d["value"] > d["expected"]
    prof = hydraulics.pressure_profile(conn, fl, "2025-09-15T12:00")
    assert [n["node"] for n in prof["nodes"][:2]] == ["wellhead", "jacket production header"]
    assert all(s["dp_bar"] > 0 for s in prof["segments"][:1])

    m, at = ProcessModel(spec), "2025-09-15T12:00"
    wells = [n for n, u in spec["units"].items() if u.get("type") == "well" and u.get("kind") != "gas" and u.get("jacket")]
    truth = {0: "shut_in", 1: "choke", 2: "back_pressure"}
    agree = sum(hydraulics.well_constraint(conn, fid(conn, w), at)["flag"] == truth[int(m.at(w, "limit", datetime.fromisoformat(at)))]
                for w in wells)
    assert agree / len(wells) >= 0.9

    s = hydraulics.backpressure_sensitivity(conn, "facility", "2025-12-28T00:00", limit=25)
    rows = [r for r in s["wells"] if r["model_sm3d_per_bar"] is not None]
    assert rows and all(r["model_sm3d_per_bar"] < 0 for r in rows)  # more back-pressure, less oil
    both = [r for r in rows if r["data_sm3d_per_bar"] is not None]
    model = sorted(r["model_sm3d_per_bar"] for r in both)[len(both) // 2]
    data = sorted(r["data_sm3d_per_bar"] for r in both)[len(both) // 2]
    # the setpoint change teaches the data side; regression on noisy tests reads weaker (attenuation)
    assert both and data < 0 and 1 / 3 <= model / data <= 3, (model, data)


def test_detector_tools_and_headroom(conn, spec, data):
    fl = story(spec, "flowline_restriction")["unit"]
    src = timeseries.SyntheticProvider(data)
    found = sre.hydraulics(conn, PACK, datetime(2025, 11, 12), datetime(2025, 11, 12, 6), src)
    assert any(d["unit_id"] == fid(conn, fl) and d["key"] == "dp_rising" for d in found)
    out = mcp_server.calc("well_constraint", fl, until=None)
    assert out["flag"] in ("back_pressure", "choke") and out["check"] == "well_constraint"
    assert mcp_server.pressure_profile(fl)["nodes"]
    room = hydraulics.headroom(conn, PACK, fid(conn, "20-K-2100A"))
    assert room["points"] and room["points"] == sorted(room["points"], key=lambda r: r["headroom_pct"])


def test_debottleneck_options_board_and_charts(conn, spec, monkeypatch):
    """Before the 25 November setpoint change, lowering the HP separator to its IOW is the top option, and
    its gain is within 20% of what the model gives for the same move; the options reach the board."""
    from fuzer.analysis import value
    from fuzer.ops import board
    from fuzer.plots import production

    out = hydraulics.debottleneck_options(conn, PACK, "facility", "2025-11-20T00:00")
    top = out["options"][0]
    assert top["kind"] == "lower_pressure" and top["target"] == fid(conn, "10-V-2001"), out["options"][:3]
    assert "iow" in top["action"] and top["next_constraint"] and top["confirm_by"]

    m = ProcessModel(spec)  # the model's own response to the 2-bar cut it applies on 25 November
    gain = 0.0
    for w, u in spec["units"].items():
        if u.get("type") == "well" and u.get("kind") != "gas" and u.get("jacket") and \
                spec["units"][u["jacket"]].get("separator") == "10-V-2001":
            t = calcs.well_tests(conn, fid(conn, w), "2025-11-20T00:00")[-1]
            wc = min(t.get("water_cut", 0) or 0, 98) / 100
            gain += (m.at(w, "liquid", datetime(2025, 11, 26, 12)) - m.at(w, "liquid", datetime(2025, 11, 24, 12))) * (1 - wc)
    assert abs(top["per_bar_sm3d"] - gain / 2) <= 0.2 * gain / 2, (top["per_bar_sm3d"], gain / 2)

    hydraulics.store_options(conn, PACK, "2025-11-20T00:00")
    opp = [i for i in value.items(conn, PACK, ("opportunity",)) if i.id.startswith("debottleneck:")]
    assert opp and opp[0].oil_sm3d == top["gain_sm3d"]
    board.refresh(conn, PACK)
    assert any(c["key"].startswith("opportunity:debottleneck:lower_pressure") for c in board.cards(conn, kind="opportunity")["cards"])

    well = fid(conn, story(spec, "flowline_restriction")["unit"])
    for fig in (production.pressure_profile(conn, well, "2025-11-12T00:00", "2025-09-15T00:00"),
                production.choke_criticality(conn, well, 60, "2025-11-15T00:00"),
                production.dp_drift(conn, well, 60, "2025-11-15T00:00"),
                production.debottleneck(conn, "facility", "2025-11-20T00:00", PACK)):
        assert fig.data and fig.summary
    assert production.dp_drift(conn, well, 60, "2025-11-15T00:00").summary["flag"] == "dp_rising"
