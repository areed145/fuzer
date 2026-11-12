"""Detections from external tools (P3.8, #703) on Kestrel: the fake predictive health service's alerts, read through
its pack's mapping, join the machine health case Fuzer's own screen opened on B1-K-2001, and an alert on a machine
the register doesn't know goes to the data steward. Its own module, as Kestrel's detectors read Kestrel's model."""

import json
from pathlib import Path

from fuzer.evals import loop
from fuzer.ops import exceptions
from fuzer.sources import packs, providers
from fuzer.sources.relative_dates import resolve_all
from fuzer.workflow import engine

PACK = Path(__file__).resolve().parents[2] / "fakeplant/bundles/kestrel/packs/healthwatch.yaml"


def test_a_providers_alert_joins_fuzers_detection_and_an_unknown_asset_goes_to_the_steward(kestrel_db):
    conn = kestrel_db
    engine.load_defaults(conn)
    loop._people(conn)
    packs.install(conn, PACK.read_text(), "test")
    loop._bring(conn, loop.arrivals(conn)["machine_health"])
    health = conn.execute("select * from exception_record where type = 'machine_health' and key = 'B1-K-2001' "
                          "and state = 'open'").fetchone()
    assert health and health["item_id"]

    sample = resolve_all(json.loads((PACK.parent.parent / "healthwatch/alerts.json").read_text()))
    out = providers.alerts(conn, "healthwatch", sample)
    assert not out["errors"], out["errors"]
    joined, lost = out["alerts"]
    assert joined["joined"] == health["id"] and joined["item_id"] == health["item_id"]  # one case, not a second
    [alert] = [x for x in exceptions.on_case(conn, health["item_id"]) if x["type"] == "health_alert"]
    f = alert["finding"]
    assert alert["source"] == "healthwatch" and f["origin"] == "external" and f["confidence"] == 0.87
    labels = {i["label"]: i for i in f["items"]}
    assert labels["Reported by"]["value"] == "HealthWatch predictive health" and labels["Severity"]["value"] == "high"
    assert labels["Provider's evidence"]["kind"] == "link"
    assert lost["to_steward"] and lost["unmatched"] == ["tag:B9-K-9001"]
    case = conn.execute("select role from work_item where id = %s", (lost["item_id"],)).fetchone()
    assert case["role"] == "data_steward"

    again = providers.alerts(conn, "healthwatch", {**sample[0], "status": "resolved"})
    assert again["alerts"] == [{"key": "HW-KR-0001", "cleared": True}]
    conn.commit()

    report = packs.conformance(conn, PACK)  # a push-only pack conforms: it installs twice, its sample alerts read
    assert not report["problems"], report["problems"]
    assert report["alerts"] == {"healthwatch": 2}


def test_a_provider_posts_its_own_alerts_behind_its_secret_and_its_pack_exports_again(kestrel_db, monkeypatch):
    """The alerts hook takes the provider's JSON only with the system's secret, and the exported pack carries the
    provider's mapping, so it goes to another project as it came."""
    import yaml
    from fastapi.testclient import TestClient

    from fuzer import api

    packs.install(kestrel_db, PACK.read_text(), "test")
    kestrel_db.commit()
    monkeypatch.setenv("KESTREL_HEALTHWATCH_HOOK", "hw-secret")
    alert = {"alert_id": "HW-KR-0100", "asset_tag": "B1-K-2001", "severity": "low", "probability": 30,
             "message": "oil analysis due", "detected_at": "2026-01-01T00:00", "status": "open"}
    with TestClient(api.app) as client:
        assert client.post("/hooks/healthwatch/alerts", json=alert).status_code == 401
        assert client.post("/hooks/healthwatch/alerts", json=alert,
                           headers={"X-Fuzer-Secret": "guess"}).status_code == 401
        r = client.post("/hooks/healthwatch/alerts", json=[alert], headers={"X-Fuzer-Secret": "hw-secret"})
        assert r.status_code == 200, r.text
        assert r.json()["provider"] == "healthwatch" and r.json()["alerts"][0]["key"] == "HW-KR-0100"
        bad = client.post("/hooks/healthwatch/alerts", json={"severity": "low"}, headers={"X-Fuzer-Secret": "hw-secret"})
        assert bad.status_code == 200 and bad.json()["errors"]  # one alert the mapping can't read is reported
        exported = yaml.safe_load(client.get("/sources/systems/healthwatch/pack").json()["yaml"])
    assert exported["detection_providers"][0]["name"] == "healthwatch"
    assert exported["detection_providers"][0]["severity"]["map"]["critical"] == 1.0
