"""Detections from external tools (P3.8, #703) on Okan: the same fake predictive health service, its own sample of
alerts, joins the machine health case on 20-K-2200B (see test_providers_kestrel for Kestrel)."""

import json
from pathlib import Path

from fuzer.evals import loop
from fuzer.sources import packs, providers
from fuzer.workflow import engine

PACK = Path(__file__).resolve().parents[2] / "fakeplant/bundles/okan/packs/healthwatch.yaml"


def test_a_providers_alert_joins_fuzers_detection_on_okan(okan_db):
    conn = okan_db.conn
    engine.load_defaults(conn)
    loop._people(conn)
    packs.install(conn, PACK.read_text(), "test")
    loop._bring(conn, loop.arrivals(conn)["machine_health"])
    health = conn.execute("select * from exception_record where type = 'machine_health' and key = '20-K-2200B' "
                          "and state = 'open'").fetchone()
    assert health and health["item_id"]
    out = providers.alerts(conn, "healthwatch", json.loads((PACK.parent.parent / "healthwatch/alerts.json").read_text()))
    assert not out["errors"], out["errors"]
    joined, lost = out["alerts"]
    assert joined["joined"] == health["id"] and joined["item_id"] == health["item_id"]
    assert lost["to_steward"] and lost["unmatched"] == ["tag:99-K-9900"]
    conn.commit()
