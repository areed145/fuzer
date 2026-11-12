"""Flare attribution over a review window (#351), on the Okan field: two export curtailments in February and March
2025 flare hard and are matched to their downtime and log entries; one spike in November has nothing around it and is
reported with its cause unknown. Evidence only: not an emissions inventory or a compliance finding."""

from fastapi.testclient import TestClient

from fuzer.agents import mcp_server
from fuzer.analysis import flare_review as fr
from fuzer.plots import troubleshooting


def test_episodes_with_their_records_or_cause_unknown(okan_db):
    out = fr.review(okan_db.conn)
    s = out["summary"]
    assert s["days"] == len(out["days"]) >= 360 and s["episodes"] == 3
    assert s["attributed"] == 2 and s["unknown"] == 1
    known = [e for e in out["episodes"] if e["attributed"]]
    assert all(any(c["kind"] == "downtime" and "EXT-EGP" in c["text"] for c in e["causes"]) for e in known)
    unknown = next(e for e in out["episodes"] if not e["attributed"])
    assert unknown["causes"] == [] and unknown["start"].startswith("2025-11")
    assert s["explained_ksm3"] == sum(e["volume"] for e in known)
    assert s["over_baseline_ksm3"] == round(sum(e["volume"] for e in out["episodes"]), 1)
    assert 50 < s["explained_share_pct"] < 100 and set(s["by_reason_ksm3"]) == {"EXT-EGP", "unknown"}
    assert "not an emissions inventory" in out["note"] and "no permit limit" in out["note"]


def test_each_day_has_its_baseline_from_the_days_before(okan_db):
    out = fr.review(okan_db.conn, "2025-02-01", "2025-03-01")
    spike = next(d for d in out["days"] if d["day"] == "2025-02-10")
    assert spike["flare_ksm3"] >= 1.5 * spike["baseline_ksm3"]
    assert all(d["baseline_ksm3"] is not None for d in out["days"])  # the 30 days before the window count
    assert [e["start"][:10] for e in out["episodes"]] == ["2025-02-10"]


def test_flare_attribution_reaches_people_and_agents(okan_db):
    from fuzer import api
    r = TestClient(api.app).get("/incidents/flare")
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["unknown"] == 1
    out = mcp_server.flare_attribution()
    assert len(out["days"]) <= 31 and out["summary"]["episodes"] == 3
    fig = troubleshooting.flare_attribution(okan_db.conn)
    assert fig.data[0]["name"] == "flare (kSm3/d)" and len(fig.data[2]["x"]) == 3
    assert fig.data[2]["marker"]["symbol"].count("circle-open") == 1
    okan_db.conn.rollback()
