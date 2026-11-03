"""Views and agent tools (#105 E, F): an upset on the plant model, a point against its window, the loss
waterfall, a score breakdown, flare; compare_periods and similar_cases."""

import os
import tempfile
from pathlib import Path

import pytest

from fakeplant import okan
from fuzer import cases, mcp_server, pipeline, plots, queries, sre
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.plots import general, troubleshooting
from fuzer.sources import ingest

PACK = load_pack("okan")


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
        sre.scan(c, PACK, "2024-11-05T00:00", "2024-11-05T12:00")
        pipeline.rebuild(c, PACK)
        yield c
    mp.undo()


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_propagation_puts_the_cause_first(conn):
    trip = next(u for u in sre.upsets(conn, "2024-11-05T00:00") if "20-K-2100C" in (u["units"] or []))
    fig = troubleshooting.propagation(conn, trip["id"])
    plots.check(fig)
    s = fig.summary
    assert s["first_cause"] == "20-V-2001" and s["order"][0] == {"unit": "20-V-2001", "minutes_after_first": 0}
    assert s["units"] > 10 and s["links_drawn"] > 0


def test_window_waterfall_breakdown_flare(conn):
    lt = conn.execute("select fuzer_id from entity_label join entity e using (fuzer_id) where e.kind = 'timeseries' "
                      "and label = 'OK_GGCP:20-LT-2101C.PV'").fetchone()["fuzer_id"]
    w = general.operating_window(conn, lt, "2024-11-01T00:00", "2024-11-14T00:00", PACK)
    assert w.summary["minutes_outside"] > 0 and w.summary["limits"]
    wf = general.loss_waterfall(conn, "2024-11-01", "2024-12-01", PACK)
    s = wf.summary
    assert s["potential_sm3"] > s["actual_sm3"] and s["unplanned_sm3"] and s["lost_usd"] > 0
    assert s["potential_sm3"] - s["actual_sm3"] >= sum(s["unplanned_sm3"].values()) + s["planned_sm3"] - 1
    wo = conn.execute("select e.fuzer_id from entity e join golden g using (fuzer_id) where e.kind = 'work_order' "
                      "and g.field = 'status' and g.value #>> '{}' <> 'TECO' limit 1").fetchone()
    sb = general.score_breakdown(conn, wo["fuzer_id"], PACK)
    assert sb.summary["value_usd"] >= 0 and sb.data[0]["x"][:3] == ["production", "risk", "compliance"]
    fl = general.flare(conn, "2024-11-01", "2024-12-01")
    assert fl.summary["co2_t"] > 0 and "estimate" in fl.summary["note"]
    for f in (w, wf, sb, fl):
        plots.check(f)


def test_compare_periods_and_similar_cases(conn):
    # OK-J12-02's eroded choke was replaced on 15 October 2025 and opened back to its first setting
    out = cases.compare_periods(conn, fid(conn, "OK-J12-02"), ("2025-10-05T00:00", "2025-10-14T00:00"),
                                ("2025-10-16T00:00", "2025-10-25T00:00"))
    choke = next(p for p in out["points"] if p["point"].endswith("CHOKE"))
    assert out["flag"] == "changed" and choke["significant"] and choke["change"] > 0
    quiet = cases.compare_periods(conn, fid(conn, "OK-J01-01"), ("2025-06-01T00:00", "2025-06-05T00:00"),
                                  ("2025-06-06T00:00", "2025-06-10T00:00"))
    assert quiet["value"] <= len(quiet["points"])
    sim = cases.similar_cases(conn, fid(conn, "20-K-2200B"))
    assert sim["cases"] and all("fixes" in c and "held" in c for c in sim["cases"])
    assert mcp_server.similar_cases("20-K-2200B")["incident"] == sim["incident"]
    up = sre.upsets(conn, "2024-11-05T00:00")[0]
    try:
        assert cases.similar_cases(conn, f"upset:{up['id']}")["incident"]
    except cases.CalcError as e:
        assert "incident" in str(e)
