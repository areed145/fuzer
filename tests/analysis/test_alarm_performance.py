"""P3.10a (#840): alarm performance as work for the alarm owner, never a list to acknowledge. On Okan, the 20-K-2300A
trip of June 2025 floods the alarm system (one case with its top contributors), and the PP test separator's level
alarms over and over (one bad actor case, quiet until resolved or back after a fix)."""

from datetime import datetime, timedelta

import pytest

from fuzer.analysis import alarm_performance as ap
from fuzer.core import detectors
from fuzer.core.pack import load_pack

PACK = load_pack("okan")


@pytest.mark.unit
def test_a_flood_counts_new_alarms_only_and_names_its_top_contributors():
    t0 = datetime(2025, 1, 1)
    on = [{"start": t0, "point": "A", "point_id": "a"}] * 20  # already on when the window opens: not new
    burst = [{"start": t0 + timedelta(hours=1, minutes=i % 5), "point": "B" if i < 8 else f"P{i}", "point_id": f"p{i}"}
             for i in range(15)]
    floods = ap._floods(on + burst, t0, 10)
    assert len(floods) == 1 and floods[0]["alarms"] == 15 and floods[0]["top"][0] == {"point": "B", "alarms": 8}
    seen = t0 + timedelta(days=1)
    assert not ap._recurs({"kinds": ["standing"], "since": t0}, seen)  # still the same standing alarm
    assert ap._recurs({"kinds": ["standing"], "since": seen + timedelta(hours=2)}, seen)
    assert ap._recurs({"kinds": ["repeating"], "starts": [seen + timedelta(hours=h) for h in range(1, 13)]}, seen)


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


def test_a_flood_is_one_case_and_a_bad_actor_is_raised_once(conn):
    from fuzer.core import work
    from fuzer.workflow import engine

    engine.load_defaults(conn)
    conn.commit()
    out = detectors.run(conn, PACK, "alarm_performance", until="2025-06-25T00:00")
    floods = [r for r in out["raised"] if r["type"] == "alarm_flood"]
    assert len(floods) == 1 and out["floods"] == 1, out
    f = conn.execute("select * from exception_record where id = %s", (floods[0]["exception_id"],)).fetchone()
    assert "2025-06-24 14:" in f["title"] and "Top contributor 1" in str(f["evidence"])
    assert "not an alarm to acknowledge" in str(f["evidence"])

    level = "OKA_PP.10LT2004.PV"
    first = detectors.run(conn, PACK, "alarm_performance", until="2025-02-12T00:00")
    assert [r["key"] for r in first["raised"] if r["type"] == "alarm_bad_actor"] == [level], first
    x = conn.execute("select * from exception_record where type = 'alarm_bad_actor' and key = %s and state = 'open'",
                     (level,)).fetchone()
    assert "repeating" in x["title"] and conn.execute(
        "select role from work_item where id = %s", (x["item_id"],)).fetchone()["role"] == "engineer"
    again = detectors.run(conn, PACK, "alarm_performance", until="2025-02-13T00:00")
    assert [r["new"] for r in again["raised"] if r["key"] == level] == [False]  # still going: the same case, quiet
    work.close_case(conn, x["item_id"], "done", "engineer:ana", reason="alarm deadband widened")
    conn.commit()
    assert level in detectors.run(conn, PACK, "alarm_performance", until="2025-02-13T00:00")["quiet"]
    back = detectors.run(conn, PACK, "alarm_performance", until="2025-09-10T00:00")  # repeating again months on
    assert [r["new"] for r in back["raised"] if r["key"] == level] == [True], back
    conn.commit()


def test_the_view_measures_and_links_cases_without_listing_alarms_to_acknowledge(conn):
    s = ap.summary(conn, PACK, until="2025-06-25T00:00")
    assert s["window"] == ["2025-06-23T00:00", "2025-06-25T00:00"] and s["alarms"] and s["target_per_hour"]
    assert len(s["floods"]) == 1 and s["floods"][0]["top"] and "not alarms to acknowledge" in s["note"]
    assert set(s) >= {"per_hour", "floods", "bad_actors", "open_cases"} and "acknowledge" not in str(s["bad_actors"])
    conn.rollback()
