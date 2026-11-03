"""Clustering (#106b): train A's modes recover the process model's (normal, fouled, tripped); train C's
two November trips are one recurring upset type; the PCA model flags the trip; wells and points group."""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from fakeplant import okan
from fuzer import clusters, ingest, mcp_server, pipeline, plots, sre
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.plots import troubleshooting
from fuzer.process_model import ProcessModel

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
    mp = pytest.MonkeyPatch()
    mp.setenv("FUZER_DATA_DIR", str(data))
    mp.setenv("FUZER_CACHE_DIR", tempfile.mkdtemp(prefix="fzcache"))
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c
    mp.undo()


def train_a(conn):
    return [r["fuzer_id"] for r in conn.execute("select fuzer_id from entity_label join entity e using (fuzer_id) "
                                                "where e.kind = 'timeseries' and label like 'OK_GGCP:20-%%A.PV' order by 1")]


def test_modes_recover_the_models(conn, data):
    spec = json.loads((data / "process_model.json").read_text())
    m = ProcessModel(spec)
    foul = next(e for e in spec["events"] if e["kind"] == "fouling")
    r = clusters.modes(conn, train_a(conn), "2024-01-01T00:00", "2024-12-31T00:00", pack=PACK)
    xs, ys = [datetime.fromisoformat(p[0]).timestamp() for p in foul["points"]], [p[1] for p in foul["points"]]
    truth = []
    for t in r["times"]:
        T = datetime.fromisoformat(t)
        truth.append("tripped" if m.at("train:A", "running", T) < 0.5 else
                     "fouled" if np.interp(T.timestamp(), xs, ys) > 15 else "normal")
    assert r["value"] == 3 and "OK_GGCP:20-TT-2350A.PV" in r["regime_points"]
    assert clusters.ari(truth, r["labels"]) >= 0.7
    assert [x["name"] for x in r["modes"]][-1] == "stopped" and r["mode_before_stops"]


def test_recurring_upsets_spe_and_entities(conn):
    for a, b in (("2024-11-05T00:00", "2024-11-05T12:00"), ("2024-11-09T12:00", "2024-11-10T00:00")):
        sre.scan(conn, PACK, a, b)
    ev = clusters.events(conn, "2024-11-05T00:00", "2024-11-11T00:00")
    trips = [u["id"] for u in sre.upsets(conn, "2024-11-05T00:00", limit=1000) if "20-K-2100C" in (u["units"] or [])]
    assert len(trips) == 2
    together = [g for g in ev["groups"] if set(trips) <= {x["upset"] for x in g["upsets"]}]
    assert together and together[0]["recurring"]

    area = conn.execute("select fuzer_id from entity_label where label = 'OKAN-20'").fetchone()["fuzer_id"]
    r = clusters.spe(conn, area, ("2024-10-28T00:00", "2024-11-04T00:00"), ("2024-11-04T12:00", "2024-11-05T12:00"))
    assert r["flag"] == "anomaly" and any("2024-11-05T0" in a["start"] for a in r["anomalies"])

    wells = clusters.entities(conn, "wells")
    assert wells["value"] == 4 and sum(g["size"] for g in wells["groups"]) > 100
    pts = clusters.entities(conn, "points", train_a(conn)[:12], "2024-11-01T00:00", "2024-11-03T00:00")
    assert pts["groups"] and sum(g["size"] for g in pts["groups"]) <= 12


def test_tools_and_charts(conn, monkeypatch):
    monkeypatch.setattr(mcp_server, "PACK", PACK)
    out = mcp_server.cluster_modes(train_a(conn), "2024-01-01T00:00", "2024-12-31T00:00")
    assert out["value"] == 3 and "labels" not in out
    fig = troubleshooting.modes_chart(conn, train_a(conn), "2024-01-01T00:00", "2024-12-31T00:00", pack=PACK)
    plots.check(fig)
    area = conn.execute("select fuzer_id from entity_label where label = 'OKAN-20'").fetchone()["fuzer_id"]
    plots.check(troubleshooting.spe_chart(conn, area, "2024-10-28T00:00", "2024-11-04T00:00", "2024-11-04T12:00",
                                          "2024-11-05T12:00"))


def test_stored_studies_run_queue_and_rerun(conn, monkeypatch):
    from fastapi.testclient import TestClient

    from fuzer import api, study

    s = study.start(conn, PACK, "modes", {"scope": train_a(conn), "since": "2024-06-01T00:00", "until": "2024-09-30T00:00"},
                    "alice")
    assert s["status"] == "done" and "modes on" in s["summary"] and s["figure_id"] and "labels" not in s["result"]
    again = study.rerun(conn, PACK, s["id"], "bob")
    assert again["id"] != s["id"] and again["params"] == s["params"] and again["summary"] == s["summary"]

    big = study.start(conn, PACK, "correlation_matrix", {"scope": "facility", "since": "2024-11-04T00:00",
                                                         "until": "2024-11-05T00:00"}, "alice")
    assert big["status"] == "queued"
    assert study.run_queued(conn, PACK) == {"ran": 1, "done": 1, "failed": 0}
    assert study.get(conn, big["id"])["status"] == "done"
    bad = study.start(conn, PACK, "correlate", {"target": "fz-nope", "since": "2024-11-04T00:00", "until": "2024-11-05T00:00",
                                                "scope": train_a(conn)}, "alice", now=True)
    assert bad["status"] == "failed" and bad["error"]

    monkeypatch.setattr(api, "PACK", PACK)
    client = TestClient(api.app)
    assert client.get(f"/studies/{s['id']}").json()["kind"] == "modes"
    assert client.get("/studies").json()[0]["id"] == bad["id"]
    monkeypatch.setattr(mcp_server, "PACK", PACK)
    got = mcp_server.get_study(s["id"])
    assert got["status"] == "done" and len(got["result"]["modes"]) <= 15
