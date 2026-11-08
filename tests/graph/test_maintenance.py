"""SAP notifications and work orders with ISO 14224 codes, at tag / equipment / model / class level."""


import pytest
from psycopg.types.json import Jsonb
from test_llm_links import (
    FakeClaude,  # a test module: tests/conftest.py puts every test folder on the path
)

from fuzer.core.pack import load_pack
from fuzer.graph import llm_links, queries, quality
from fuzer.ops import pipeline

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def notification_with_damage_code(conn, code):
    return conn.execute(
        "select r.source_key from source_record r join crosswalk c using (source, record_type, source_key) "
        "where r.source = 'sap' and r.record_type = 'notification' and r.payload->>'damage_code' = %s "
        "order by r.source_key limit 1",
        (code,),
    ).fetchone()


def test_site_codes_map_to_iso(conn):
    r = notification_with_damage_code(conn, "D01")
    g = queries.entity(conn, fid(conn, r["source_key"]))["golden"]
    assert g["failure_mode"]["value"] == "VIB" and g["failure_mode_name"]["value"] == "Vibration"
    assert g["failure_mechanism_name"]["value"] == "Vibration"


def test_unmapped_site_codes_are_reported(conn):
    record = notification_with_damage_code(conn, "D01")
    fid_ = fid(conn, record["source_key"])
    conn.execute(
        "insert into golden (fuzer_id, field, value, source, rule) values (%s, 'unmapped_codes', %s, 'sap', 'test')",
        (fid_, Jsonb([{"list": "damage", "code": "D99"}])),
    )
    result = quality.unmapped_site_codes(conn)
    conn.execute("delete from golden where fuzer_id = %s and field = 'unmapped_codes'", (fid_,))
    conn.commit()  # the row is gone again; don't hold its lock for the next test
    assert not result["passed"] and result["count"] == 1
    assert result["examples"][0]["code"] == "D99"


def test_history_at_tag_and_equipment(conn):
    tag = queries.maintenance(conn, fid(conn, "20-K-2200B"))
    assert tag["total"] > 0 and tag["recent"][0]["created"] >= tag["recent"][-1]["created"]

    pump = conn.execute("select src from edge where dst = %s and rel = 'installed_at' and valid_to is null",
                        (fid(conn, "20-K-2200B"),)).fetchone()["src"]
    assert queries.maintenance(conn, pump)["total"] >= 1


def test_history_rolls_up_to_model_and_class(conn):
    pump = conn.execute("select src from edge where dst = %s and rel = 'installed_at' and valid_to is null",
                        (fid(conn, "20-K-2200B"),)).fetchone()["src"]
    model = conn.execute("select dst from edge where src = %s and rel = 'instance_of'", (pump,)).fetchone()["dst"]
    by_model = queries.maintenance(conn, model)
    by_class = queries.maintenance(conn, fid(conn, "ISO14224:CO"))
    everything = conn.execute("select count(*) as n from entity where kind = 'notification'").fetchone()["n"]
    assert 0 < queries.maintenance(conn, pump)["total"] <= by_model["total"] <= by_class["total"] < everything
    codes = {m["code"] for m in by_class["failure_modes"]}
    assert codes


def test_since_filter(conn):
    all_ = queries.maintenance(conn, fid(conn, "ISO14224:CO"))
    recent = queries.maintenance(conn, fid(conn, "ISO14224:CO"), since="2024-01-01")
    assert 0 < recent["total"] < all_["total"]
    assert all(n["created"] >= "2024-01-01" for n in recent["recent"])


def test_notifications_link_to_logged_events(conn):
    llm_links.run(conn, PACK, client=FakeClaude(), workers=1)
    stats = pipeline.rebuild(conn, PACK)
    assert stats["maintenance_event_links"] >= 1
    notification = conn.execute("select source_key from source_record where source = 'sap' and record_type = 'notification' "
                                "and payload->>'short_text' like %s limit 1", ("%Train B 2nd stage compressor%",)).fetchone()
    rows = conn.execute(
        """
        select g.value #>> '{}' as summary from edge e join golden g on g.fuzer_id = e.dst and g.field = 'summary'
        where e.src = %s and e.rel = 'related_to'
        """, (fid(conn, notification["source_key"]),)).fetchall()
    assert any("tripped" in r["summary"] for r in rows)


def test_api_and_ontology(conn):
    from fastapi.testclient import TestClient

    from fuzer.api import app
    r = TestClient(app).get(f"/entities/{fid(conn, '20-K-2200B')}/maintenance", params={"limit": 2})
    assert r.status_code == 200 and 0 < len(r.json()["recent"]) <= 2
    rels = {(x["src_kind"], x["rel"], x["dst_kind"]) for x in queries.ontology(conn)["relations"]}
    assert {("notification", "concerns", "equipment"), ("work_order", "for_notification", "notification")} <= rels
