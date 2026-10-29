"""SAP notifications and work orders with ISO 14224 codes, at tag / equipment / model / class level."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

from fuzer import db, fakedata, ingest, llm_links, pipeline, queries
from fuzer.pack import load_pack

sys.path.insert(0, str(Path(__file__).parent))
from test_llm_links import FakeClaude  # noqa: E402

PACK = load_pack()


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    fakedata.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_site_codes_map_to_iso(conn):
    g = queries.entity(conn, fid(conn, "10000065"))["golden"]  # the planted notification
    assert g["failure_mode"]["value"] == "VIB" and g["failure_mode_name"]["value"] == "Vibration"
    assert g["failure_mechanism_name"]["value"] == "Clearance / alignment failure"


def test_history_at_tag_and_equipment(conn):
    tag = queries.maintenance(conn, fid(conn, "20-P-1001B"))
    assert tag["total"] > 0 and tag["recent"][0]["created"] >= tag["recent"][-1]["created"]
    planted = next(n for n in tag["recent"] if n["text"] == "Separator oil pump B tripped on high vibration")
    assert planted["work_orders"][0]["activity"] == "Adjust"

    pump = conn.execute("select src from edge where dst = %s and rel = 'installed_at' and valid_to is null",
                        (fid(conn, "20-P-1001B"),)).fetchone()["src"]
    assert queries.maintenance(conn, pump)["total"] >= 1


def test_history_rolls_up_to_model_and_class(conn):
    pump = conn.execute("select src from edge where dst = %s and rel = 'installed_at' and valid_to is null",
                        (fid(conn, "20-P-1001B"),)).fetchone()["src"]
    model = conn.execute("select dst from edge where src = %s and rel = 'instance_of'", (pump,)).fetchone()["dst"]
    by_model = queries.maintenance(conn, model)
    by_class = queries.maintenance(conn, fid(conn, "ISO14224:PU"))
    everything = conn.execute("select count(*) as n from entity where kind = 'notification'").fetchone()["n"]
    assert queries.maintenance(conn, pump)["total"] <= by_model["total"] <= by_class["total"] < everything
    codes = {m["code"] for m in by_class["failure_modes"]}
    assert codes <= {"VIB", "ELP", "FTS", "LOO", "NOI"}  # pump damage codes only


def test_since_filter(conn):
    all_ = queries.maintenance(conn, fid(conn, "ISO14224:PU"))
    recent = queries.maintenance(conn, fid(conn, "ISO14224:PU"), since="2024-01-01")
    assert 0 < recent["total"] < all_["total"]
    assert all(n["created"] >= "2024-01-01" for n in recent["recent"])


def test_notifications_link_to_logged_events(conn):
    llm_links.run(conn, PACK, client=FakeClaude(), workers=1)
    stats = pipeline.rebuild(conn, PACK)
    assert stats["maintenance_event_links"] >= 1
    rows = conn.execute(
        """
        select g.value #>> '{}' as summary from edge e join golden g on g.fuzer_id = e.dst and g.field = 'summary'
        where e.src = %s and e.rel = 'related_to'
        """, (fid(conn, "10000065"),)).fetchall()
    assert any("tripped" in r["summary"] for r in rows)  # the 2024-01-01 logged trip, one day before


def test_api_and_ontology(conn):
    from fastapi.testclient import TestClient
    from fuzer.api import app
    r = TestClient(app).get(f"/entities/{fid(conn, '20-P-1001B')}/maintenance", params={"limit": 2})
    assert r.status_code == 200 and len(r.json()["recent"]) == 2
    rels = {(x["src_kind"], x["rel"], x["dst_kind"]) for x in queries.ontology(conn)["relations"]}
    assert {("notification", "concerns", "equipment"), ("work_order", "for_notification", "notification")} <= rels
