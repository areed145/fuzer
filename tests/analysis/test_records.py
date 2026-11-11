"""Records mismatch (#344) on the Okan fake plant: the planted downtime with no log or notification and the logged
stop with no downtime are found, the stories' own records match, the tolerances are settings stated in the output,
and the gaps reach the ops lead queue and the shift handover as questions, clearing when they no longer hold."""

from datetime import UTC, datetime

import pytest

from fuzer.analysis import records
from fuzer.core import settings

WEEK = ("2025-12-23T00:00", "2025-12-31T00:00")


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


def _set(conn, key, value):
    settings.set(conn, key, value, by="test")
    conn.commit()


def _reset(conn, *keys):
    for k in keys:
        settings.reset(conn, k, by="test")
    conn.commit()


def test_the_planted_mismatches_are_found_with_the_tolerances_stated(conn):
    m = records.mismatch(conn, *WEEK)
    assert m["tolerance"] == {"log_hours": 12, "notification_days": 3, "notification_for_categories": ["equipment"]}
    assert "12 h" in m["basis"] and m["records_through"].startswith("2025-12-30")
    [dt] = m["downtime_without_records"]
    assert dt["at"] == "2025-12-27T02:40" and dt["about"][0] == "40-V-2002" and dt["reason"] == "Process trip"
    assert dt["missing"] == ["log", "notification"] and dt["lost_oil_sm3"] == 300
    [lg] = m["log_stops_without_downtime"]
    assert lg["about"] == ["OK-J09-02"] and lg["missing"] == ["downtime"] and "shut in" in lg["text"]
    assert "not proof" in m["caveats"][0]


def test_the_stories_own_records_match(conn):
    m = records.mismatch(conn, "2024-03-01", "2025-04-01")
    flagged = {r["at"] for r in m["downtime_without_records"]}
    # a trip logged and raised the same day (20-K-2200B), carryover trips with their notification three days off,
    # an EGP curtailment logged as an export cut against the facility, hydrates logged on the well
    assert not flagged & {"2024-03-14T03:20", "2024-11-09T18:15", "2025-02-10T08:00", "2025-01-14T05:00"}
    assert not m["log_stops_without_downtime"]  # every trip the stories log has its downtime
    # the train A trips were logged, but the notification only came after the third
    june = next(r for r in m["downtime_without_records"] if r["at"] == "2024-06-11T15:00")
    assert june["missing"] == ["notification"] and june["logs"]


def test_tolerances_are_settings(conn):
    try:
        _set(conn, "records.log_tolerance_hours", 72)  # wide enough that the well's shut-in falls on the trip's day
        m = records.mismatch(conn, *WEEK)
        assert m["tolerance"]["log_hours"] == 72 and "72 h" in m["basis"]
        assert not m["log_stops_without_downtime"]
        assert [r["missing"] for r in m["downtime_without_records"]] == [["notification"]]
        _set(conn, "records.notification_categories", "")
        assert not records.mismatch(conn, *WEEK)["downtime_without_records"]
    finally:
        _reset(conn, "records.log_tolerance_hours", "records.notification_categories")


def test_too_little_data_is_a_result(conn):
    m = records.mismatch(conn, "2019-01-01", "2019-02-01")
    assert m["downtime_checked"] == 0 and m["stop_entries_checked"] == 0
    assert any("nothing to compare" in c for c in m["caveats"])


def test_gaps_reach_the_ops_lead_queue_and_the_handover_and_clear(conn):
    from fuzer.ops import handover

    out = records.watch(conn)
    assert len(out["raised"]) == 2 and all(r["new"] for r in out["raised"])
    rows = conn.execute("select e.*, w.role, w.state as case_state from exception_record e "
                        "join work_item w on w.id = e.item_id where e.type = 'records_gap' order by e.id").fetchall()
    assert [r["role"] for r in rows] == ["ops_lead", "ops_lead"]
    assert rows[0]["title"].startswith("Downtime on 40-V-2002 27 Dec 02:40 (Process trip): no log entry and no notification")
    assert rows[1]["title"] == "Log says OK-J09-02 stopped 29 Dec 19:00: no downtime record"
    said = {i["label"]: i["value"] for i in rows[0]["evidence"]["finding"]["items"]}
    assert "12 h" in said["Matched within"] and "not proof" in said["Note"]
    assert {"40-V-2002"} <= {x for x in said["About"].split(", ")}
    assert rows[0]["evidence"]["finding"]["origin"] == "deterministic" and rows[0]["evidence"]["at"]
    assert not any(r["new"] for r in records.watch(conn)["raised"])  # a second run updates, it doesn't duplicate

    now = datetime.now(UTC)
    f = handover.facts(conn, now.replace(year=now.year - 1), now)
    lines = f["sections"]["Records to check"]
    assert {c for x in lines for c in x["cites"]} >= {f"exception:{r['id']}" for r in rows}
    assert not any(f"case:{r['item_id']}" in x["cites"] for x in f["sections"]["Open work"] for r in rows)
    conn.rollback()

    try:
        _set(conn, "records.log_tolerance_hours", 72)
        _set(conn, "records.notification_categories", "")
        cleared = records.watch(conn)["cleared"]
        assert set(cleared) == {r["key"] for r in rows}
        states = [r["state"] for r in conn.execute(
            "select w.state from work_item w where w.id = any(%s)", ([r["item_id"] for r in rows],))]
        assert states and all(s != "open" for s in states)
        conn.rollback()
    finally:
        _reset(conn, "records.log_tolerance_hours", "records.notification_categories")


def test_the_tool_returns_the_calc(conn):
    from fuzer.agents import mcp_server

    out = mcp_server.records_mismatch(*WEEK)
    assert out["tolerance"]["log_hours"] == 12 and len(out["downtime_without_records"]) == 1
