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
    [k] = exceptions.match(conn, ["tag:B1-K-2001"])[0]  # the module shares one database: start from no open work on
    for x in conn.execute("select type, key from exception_record where state = 'open' and %s = any(subjects)", (k,)):
        exceptions.clear(conn, x["type"], x["key"], "test", reason="a fresh start, whatever ran before in the module")
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


def test_a_noisy_provider_is_muted_for_one_class_or_triaged_first_and_its_close_outs_are_counted(kestrel_db):
    """Trust (P3.8b): muted for the compressors' class, a new alert is kept and counted with no case; set to triage
    first, its case goes to the triage role; and closing that case as a false alarm shows in its rates."""
    from fuzer.core import detectors, settings
    from fuzer.core.pack import resolve
    from fuzer.ops import disposition, suppression

    conn = kestrel_db
    engine.load_defaults(conn)
    loop._people(conn)
    packs.install(conn, PACK.read_text(), "test")
    [k] = exceptions.match(conn, ["tag:B1-K-2001"])[0]
    for x in conn.execute("select type, key from exception_record where state = 'open' and %s = any(subjects)", (k,)):
        exceptions.clear(conn, x["type"], x["key"], "test", reason="a fresh start, so a new alert opens its own case")
    co = detectors.classes_of(conn, [k])[k]
    alert = {"alert_id": "HW-KR-0200", "asset_tag": "B1-K-2001", "severity": "medium", "probability": 40,
             "message": "bearing wear pattern", "detected_at": "2026-01-02T00:00", "status": "open"}

    providers.set_trust(conn, "healthwatch", "healthwatch", "triage", "new vendor, look first", "ana")
    providers.set_trust(conn, "healthwatch", "healthwatch", "mute", "three false alarms on these", "ana", co)
    [muted] = providers.alerts(conn, "healthwatch", alert)["alerts"]
    assert muted["trust"] == "mute" and muted["item_id"] is None
    held = [x for x in suppression.found(conn)["items"] if x["key"] == "HW-KR-0200"]
    assert held and held[0]["window_kind"] == "muted" and "three false alarms" in held[0]["reason"]

    settings.set(conn, "providers.triage_role", "role:ops_lead", by="test", reason="the desk looks first")
    providers.set_trust(conn, "healthwatch", "healthwatch", "triage", "watch it a while", "ana", co)
    [triaged] = providers.alerts(conn, "healthwatch", {**alert, "alert_id": "HW-KR-0201"})["alerts"]
    assert triaged["trust"] == "triage" and triaged["item_id"] and not triaged["joined"], triaged
    assert conn.execute("select role from work_item where id = %s", (triaged["item_id"],)).fetchone()["role"] == "ops_lead"

    disposition.record(conn, resolve(conn), triaged["item_id"], "ana", "false_alarm", note="the pattern was a sensor")
    r = disposition.rates(conn)[("health_alert", "healthwatch")]
    assert r["false_alarm"] >= 1 and r["closed"] >= 1
    q = disposition.quality([r])
    assert q["false_alarm_rate"] == r["false_alarm"] / r["closed"]
    from fuzer.api import settings as settings_api  # what the Detection page reads

    page = settings_api.detectors_list(conn)
    [hw] = [p for p in page["providers"] if p["name"] == "healthwatch"]
    assert hw["closeouts"]["false_alarm"] == r["false_alarm"] and {t["class"] for t in hw["trust"]} == {"", co}
    assert all("closeouts" in d for d in page["detectors"])
    try:
        providers.set_trust(conn, "healthwatch", "healthwatch", "mute", " ", "ana")
    except Exception as e:
        assert "reason" in str(e)
    else:
        raise AssertionError("trust changed with no reason")
    conn.commit()


def test_a_second_provider_is_a_mapping_and_a_connection_polled_and_offered_each_close_out(kestrel_db):
    """P3.8c: CorrWatch, added with a pack and no code, writes its alerts to a CSV its connection holds. Polling reads
    it once (again only when it changes), its alert is worked like any other, and the case's close-out is offered
    back to it as a write-back a person approves. HealthWatch takes no feedback, so it is offered nothing."""
    import yaml

    from fuzer.core.pack import resolve
    from fuzer.ops import disposition, writeback

    conn = kestrel_db
    engine.load_defaults(conn)
    loop._people(conn)
    corr = PACK.parent / "corrwatch.yaml"
    packs.install(conn, corr.read_text(), "test")
    conn.commit()
    first = [x for x in providers.poll(conn, "data") if x.get("provider") == "corrwatch"]
    assert first and not first[0]["errors"], first
    [alert] = first[0]["alerts"]
    assert alert["key"] == "CW-KR-0007" and alert["item_id"] and not alert["unmatched"]
    assert next(x for x in providers.poll(conn, "data") if x.get("provider") == "corrwatch")["unchanged"]

    disposition.record(conn, resolve(conn), alert["item_id"], "ana", "false_alarm", note="probe fouled, wall is fine")
    offered = writeback.listing(conn, system="corrwatch")
    assert [(w["kind"], w["record"], w["state"], w["payload"]["disposition"]) for w in offered] == [
        ("detection_feedback", "CW-KR-0007", "awaiting_approval", "false_alarm")]
    assert not writeback.listing(conn, system="healthwatch")

    report = packs.conformance(conn, corr)
    assert not report["problems"], report["problems"]
    from fuzer.sources import catalog

    exported = yaml.safe_load(catalog.export_pack(conn, "corrwatch"))
    assert exported["detection_providers"][0]["poll"] == "corrosion_alerts"
    assert exported["detection_providers"][0]["feedback"] is True
    conn.commit()
