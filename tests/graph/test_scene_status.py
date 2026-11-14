"""P5.16b and P5.16d on the server: status for every item in a scene in one read, matching each asset's status card,
and "if this trips" from the same reads as production at risk (P5.9a)."""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from fuzer.api import app
from fuzer.graph import queries, scene
from fuzer.ops import at_risk, status_now


def _fid(conn, label: str) -> str:
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


@pytest.fixture(scope="module")
def okan(okan_db):
    conn = okan_db.conn
    s = scene.scene(conn)
    return conn, s, status_now.status(conn, [i["id"] for i in s["items"]])


def test_one_read_gives_open_work_and_alarms_for_the_whole_scene(okan):
    _conn, s, st = okan
    drawn = {i["id"] for i in s["items"]}
    assert set(st["status"]) <= drawn and st["status"], "only drawn items, and Okan has some with something to say"
    assert st["totals"]["open_work"] >= 1 and st["totals"]["alarms"] >= 1
    for v in st["status"].values():  # an item is listed only when it has something to say
        assert v["open_work"] or v["alarms"] or v["down"]


def test_the_counts_match_each_assets_status_card(okan):
    _conn, s, st = okan
    client = TestClient(app)
    work = [i for i, v in st["status"].items() if v["open_work"]]
    ringing = [i for i, v in st["status"].items() if v["alarms"]][:3]
    quiet = [i["id"] for i in s["items"] if i["id"] not in st["status"]][:2]
    for i in [*work, *ringing, *quiet]:
        card = client.get(f"/entities/{i}/status").json()
        mine = st["status"].get(i, {"open_work": 0, "alarms": 0, "down": None})
        assert (card["open_work"], card["alarms"]) == (mine["open_work"], mine["alarms"]), i
        assert bool(card["downtime"] and card["downtime"]["open"]) == bool(mine["down"]), i


def test_down_now_is_the_newest_downtime_with_no_end_as_the_card_reads_it(okan):
    conn, _s, _st = okan
    k = _fid(conn, "20-K-2200B")
    assert k not in status_now.down(conn, [k])
    newest = queries.status(conn, k)["downtime"]["id"]
    with conn.transaction():
        conn.execute("delete from golden where fuzer_id = %s and field = 'end'", (newest,))
        assert status_now.down(conn, [k])[k]["id"] == newest
        assert queries.status(conn, k)["downtime"]["open"] is True
        raise psycopg.Rollback()
    conn.rollback()  # nothing was written; end the read transaction the savepoint sat in


def test_the_api_reads_the_status_of_the_scene_it_names(okan):
    conn, _s, _st = okan
    k = _fid(conn, "20-K-2200B")
    client = TestClient(app)
    area = {i["id"] for i in client.get("/scene", params={"at": k}).json()["items"]}
    got = client.get("/scene/status", params={"at": k}).json()
    assert set(got["status"]) <= area and got["alarm_hours"] == status_now.ALARM_HOURS
    assert set(got["totals"]) == {"open_work", "alarms", "down"}


def test_a_trip_names_what_goes_down_and_the_production_at_risk_as_an_estimate(okan):
    conn, _s, _st = okan
    k = _fid(conn, "20-K-2200B")
    t = at_risk.trip(conn, k, hours=12)
    assert t["item"]["label"] == "20-K-2200B" and t["hours"] == 12
    # Not on the flow network itself: it takes down the flow units of its train, as a planned shutdown would.
    assert t["units"] and t["on_network"]
    assert t["wells"] and all(w["id"] not in {u["id"] for u in t["units"]} for w in t["wells"])
    oil = next(x for x in t["at_risk"] if x["quantity"] == "oil_rate")
    expected = sum(w["rates"]["oil_rate"]["value"] for w in t["wells"] if "oil_rate" in w["rates"]) * 12 / 24
    assert oil["value"] == pytest.approx(expected, abs=0.1) and oil["unit"]
    assert "estimate" in t["basis"].lower() and "hydraulic" in t["basis"]
    assert set(t["untested"]) == {w["label"] for w in t["wells"] if not w["rates"]}
    # Doubling the window doubles what is at risk, and the API serves the same.
    again = TestClient(app).get("/scene/trip", params={"item": k, "hours": 24}).json()
    assert next(x for x in again["at_risk"] if x["quantity"] == "oil_rate")["value"] == pytest.approx(oil["value"] * 2, abs=0.2)
    assert TestClient(app).get("/scene/trip", params={"item": k, "hours": 0}).status_code == 422


def test_a_well_that_trips_takes_only_itself(okan):
    conn, _s, _st = okan
    w = _fid(conn, "OK-J01-05")
    t = at_risk.trip(conn, w)
    assert [x["label"] for x in t["wells"]] == ["OK-J01-05"] and t["behind"] == [] and t["starved"] == []


def test_a_case_reads_its_path_as_the_links_were_at_its_time(okan):
    conn, _s, _st = okan
    k = _fid(conn, "20-K-2200B")
    now = scene.scene(conn, k)
    gone = now["links"][0]
    with conn.transaction():
        conn.execute("update edge set valid_to = now() - interval '1 day' where id = %s", (gone["id"],))
        assert gone["id"] not in {x["id"] for x in scene.scene(conn, k)["links"]}
        then = scene.scene(conn, k, as_of=datetime.now(UTC) - timedelta(days=2))
        assert gone["id"] in {x["id"] for x in then["links"]} and then["as_of"]
        raise psycopg.Rollback()
    conn.rollback()
