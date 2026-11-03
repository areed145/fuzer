"""OEM curves (#92): pump curves and choke characteristics land from vendor CSVs; datasheets are read
by Claude (a fake here) into review items, and accepted ones land too. Charts place the plant's
operating points on them, and the planted stories show: export pump A losing head until its wear rings
are replaced, OK-J12-02's choke eroding until replaced, OK-NWP-G3's choke plugging until cleared."""

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fakeplant import okan
from fuzer import plots
from fuzer.agents import mcp_server
from fuzer.analysis import curves
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.graph import pipeline, queries
from fuzer.plots import maintenance, production
from fuzer.sources import ingest, llm_curves

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
    mp.setenv("FUZER_DATA_DIR", str(data))  # synthetic values from the plant's process model
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c
    mp.undo()


@pytest.fixture
def okan_pack(monkeypatch):
    monkeypatch.setattr(mcp_server, "PACK", PACK)


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


class Reader:
    """Plays Claude reading datasheets: returns the true curve for the model named in the request."""

    def __init__(self, truth):
        from test_agents import _message

        self.truth, self._message, self.requests = {t["model"]: t for t in truth}, _message, []
        self.beta = type("B", (), {})()
        self.beta.messages = type("M", (), {})()
        self.beta.messages.create = self.create

    def create(self, **kw):
        self.requests.append(kw)
        text = kw["messages"][0]["content"][-1]["text"]
        t = next(t for m, t in self.truth.items() if f" {m})" in text)
        out = {"kind": t["kind"], "manufacturer": t["manufacturer"], "model": t["model"], "x_unit": t["units"]["x"],
               "series": [{"name": n, "unit": t["units"][n], "points": [{"x": x, "y": y} for x, y in pts]}
                          for n, pts in t["series"].items()],
               "ratings": [{"name": "cv_max", "x": t["cv_max"], "y": 0}] if t["kind"] == "choke" else
               [{"name": "bep", "x": t["q_bep"], "y": t["h_bep"]}],
               "read_from": "table", "notes": ""}
        return self._message([{"type": "text", "text": json.dumps(out)}], "end_turn")


def test_curves_land_and_chokes_are_equipment(conn):
    landed = {r["label"]: r["n"] for r in conn.execute(
        "select l.label, count(*) as n from equipment_curve e join entity_label l on l.fuzer_id = e.model_id group by 1")}
    assert "MASTER FLO P2-12" in landed and sum(1 for k in landed if k.startswith(("FLOWSERVE", "SULZER"))) == 3
    choke = fid(conn, "OK-J12-02")
    tag = conn.execute("select e.src from edge e join entity_label l on l.fuzer_id = e.src where e.rel = 'part_of' "
                       "and e.dst = %s and l.label like '%%-HCV-%%'", (choke,)).fetchone()["src"]
    assert curves.for_model(conn, curves.model_of(conn, tag))["series"]["cv"][-1] == [100.0, 12.0]
    installs = conn.execute("select valid_from from edge where rel = 'installed_at' and dst = %s order by valid_from",
                            (tag,)).fetchall()
    assert [str(r["valid_from"]) for r in installs][-1] == "2025-10-15"


def test_pump_a_loses_head_until_its_wear_rings_are_replaced(conn):
    fig = maintenance.pump_curve(conn, fid(conn, "10-P-1001A"))
    by = {m["month"]: m["head_vs_curve_pct"] for m in fig.summary["head_vs_curve_by_month"]}
    assert abs(by["2025-01"]) < 1.5 and by["2025-08"] < -5 and abs(by["2025-11"]) < 1.5
    assert fig.summary["curve_source"] == "oem" and fig.summary["worst_month"]["month"] in ("2025-08", "2025-09")
    assert 0.8 < fig.summary["share_in_preferred_region"] <= 1
    plots.check(fig) or plots.png(fig.spec)


def test_eroded_choke_on_j12_02(conn):
    fig = production.choke(conn, fid(conn, "OK-J12-02"))
    s = fig.summary
    assert s["replaced"] == ["2025-10-15"] and s["before_replacement_latest"] > 1.3 and s["reading"].startswith("eroded")
    q = {x["quarter"]: x["cv_vs_curve"] for x in s["cv_vs_curve_by_quarter"]}
    assert 0.9 < q["2024-Q3"] < 1.1 and q["2025-Q3"] > 1.3
    assert any(t["name"].startswith("since new choke") for t in fig.data)
    plots.check(fig) or plots.png(fig.spec)


def test_scanned_datasheet_read_reviewed_and_used(conn, data, okan_pack, monkeypatch):
    g3 = fid(conn, "OK-NWP-G3")
    with pytest.raises(plots.ChartError, match="MCV-40.*not read yet"):
        production.choke(conn, g3)
    text, images = llm_curves.page_content(data / "documents" / "DS-MCV-40.pdf")
    assert text == "" and len(images) == 1
    text, images = llm_curves.page_content(data / "documents" / "DS-CC30.pdf")
    assert "22.00" in text and not images

    truth = json.loads((data / "answer_keys" / "curves.json").read_text())
    reader = Reader(truth)
    out = llm_curves.run(conn, PACK, client=reader)
    assert out["stored"] == out["datasheets"] == 5 and out["queued_for_review"] == 5
    scan = next(r for r in reader.requests if "DS-MCV-40" in r["messages"][0]["content"][-1]["text"])
    assert scan["messages"][0]["content"][0]["type"] == "image"
    assert llm_curves.run(conn, PACK, client=reader)["datasheets"] == 0  # cached
    scored = llm_curves.score(conn, truth)
    assert scored["read"] == 5 and all(v == 1.0 for v in scored["within_3pct_by_form"].values())

    item = conn.execute("select id from review_item where kind = 'curve' and payload->>'doc_number' = 'DS-MCV-40'").fetchone()
    from fuzer import api

    monkeypatch.setattr(api.rebuilder, "pack", PACK)  # the API's rebuild runs on this asset's pack
    client = TestClient(api.app)
    assert client.post(f"/review/{item['id']}", json={"decision": "accept", "by": "test"}).status_code == 200
    api.rebuilder.wait()
    conn.rollback()
    fig = production.choke(conn, g3)
    s = fig.summary
    assert s["curve_source"] == "datasheet:DS-MCV-40" and "scaled" in s["note"]
    assert s["furthest"]["cv_vs_curve"] < 0.75 and s["furthest"]["at"][:7] in ("2025-08", "2025-09")
    assert s["reading"].startswith("on its characteristic")  # cleared by the bean change
    got = mcp_server.plot_choke("OK-NWP-G3")
    assert got["marker"].startswith("[[figure:") and "reading" in got["summary"]
    assert mcp_server.plot_pump_curve("10-P-1001A")["summary"]["worst_month"]
    pump = client.get(f"/entities/{fid(conn, '10-P-1001A')}/curves").json()
    assert pump["curve"] == "pump" and "head" in pump["series"]
