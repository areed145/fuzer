"""Alarms, trips and states (#105 D) on Okan's November 2024 week: floods at train C's two trips, the
failed gas lift valve's standing low casing pressure, the hunting level transmitter chattering."""

import os
import tempfile
from pathlib import Path

import pytest

from fakeplant import okan
from fuzer import alarms, db, ingest, mcp_server, pipeline, plots
from fuzer.pack import load_pack
from fuzer.plots import troubleshooting

PACK = load_pack("okan")
WEEK = ("2024-11-04T00:00", "2024-11-11T00:00")


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzokan"))
    okan.generate(data)
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATA_DIR", str(data))
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c
    mp.undo()


def test_alarm_analysis_reads_the_week(conn):
    a = alarms.alarm_analysis(conn, PACK, None, *WEEK)
    assert a["flag"] == "flood" and a["unit"] == "alarms per hour" and a["value"] > 0
    starts = [f["start"][:13] for f in a["floods"]]
    assert "2024-11-05T05" in starts and "2024-11-09T18" in starts  # train C's two trips
    assert any(s["point"] == "OK_J07_03.CASP" for s in a["standing"])  # its gas lift valve failed open in September
    assert any(c["point"] == "OK_GGCP:20-LT-2001.PV" for c in a["chattering"])
    assert a["bad_actors"] and a["bad_actors"][0]["alarms"] >= a["bad_actors"][-1]["alarms"]


def test_trips_and_states(conn):
    t = alarms.trip_analysis(conn, PACK, None, "2024-11-01T00:00", "2024-11-14T00:00")
    by = {u["unit"]: u for u in t["units"]}
    for m in ("20-GT-2000C", "20-K-2100C", "20-K-2200C", "20-K-2300C"):
        assert by[m]["stops"] == 2 and by[m]["starts"] == 2, by[m]
    assert by.get("20-K-2100A", {"stops": 0})["stops"] == 0 and "first-out" in t["note"]  # train A ran on
    s = alarms.states(conn, PACK, None, "2024-11-05T00:00", "2024-11-06T00:00")
    k = s["units"]["20-K-2100C"]["spans"]
    assert [x["state"] for x in k][:3] == ["running", "transition", "stopped"] and k[2]["start"] <= "2024-11-05T06:10"


def test_charts_and_tools(conn, monkeypatch):
    monkeypatch.setattr(mcp_server, "PACK", PACK)
    tl = troubleshooting.state_timeline(conn, None, "2024-11-05T00:00", "2024-11-06T00:00", PACK)
    plots.check(tl)
    assert "20-K-2100C" in tl.summary["changed_state"] and tl.summary["first_to_stop"]
    al = troubleshooting.alarms_chart(conn, None, *WEEK, PACK)
    plots.check(al)
    assert max(al.data[0]["y"]) > alarms.FLOOD
    assert mcp_server.trip_analysis(*WEEK)["value"] >= 8
    got = mcp_server.plot_state_timeline("2024-11-05T00:00", "2024-11-06T00:00")
    assert got["marker"].startswith("[[figure:")
