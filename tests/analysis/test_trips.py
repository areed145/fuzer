"""Compressor trip diagnosis on the Okan stories: each train tripped for its own reason (train B on 20-K-2200B's
bearing vibration, train A on 20-K-2300A's discharge temperature behind a fouled after-cooler, train C on liquid
carried over from the inlet separator), and the data says which moved first, how long before, and what to check
before the restart. A second trip of the same kind inside 30 days is an inspection, not another restart. Raised for
the IOC, each trip carries its restart checklist to the operator sent to the train."""

import pytest

from fuzer.analysis import rotating, trips
from fuzer.analysis.calcs import CalcError
from fuzer.core.pack import load_pack
from fuzer.graph import queries

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    from fuzer.workflow import engine

    engine.load_defaults(okan_db.conn)
    okan_db.conn.commit()
    yield okan_db.conn


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def said(row) -> dict:
    """What a raised exception's finding says, by the label a person reads it under."""
    return {i["label"]: i["value"] for i in row["evidence"]["finding"]["items"]}


def test_train_b_tripped_on_the_bearing_vibration_it_showed_for_hours(conn):
    d = trips.diagnose(conn, fid(conn, "20-K-2200B"), "2024-03-14T03:20", pack=PACK)
    assert d["value"] == "vibration" and d["cause"] == "high vibration" and d["train"].startswith("train B")
    first = d["first_out"]
    assert first["point"].endswith("20-VT-2200B.PV") and first["unit"] == "20-K-2200B" and first["lead_minutes"] >= 60
    assert any(x["point"].endswith("20-VT-2200B.PV") and x["value"] >= x["trip_setting"] for x in d["tripped_on"])
    assert d["verdict"] == "restart_after_checks" and any("API 670" in c for c in d["checklist"])
    assert d["tripped_at"].startswith("2024-03-14T03") and "ISO 13379-1" in d["basis"]


def test_a_second_vibration_trip_in_thirty_days_means_inspect_before_restart(conn):
    d = trips.diagnose(conn, fid(conn, "20-K-2100B"), "2024-03-29T22:10", pack=PACK)  # any machine of the train
    assert d["value"] == "vibration" and d["verdict"] == "inspect_first" and d["flag"] == "inspect_first"
    assert d["prior_trips"][0]["family"] == "vibration" and 15 <= d["prior_trips"][0]["days_before"] <= 16
    assert d["checklist"][0].startswith("Second high vibration trip") and "Inspect before restarting" in d["title"]


def test_train_a_tripped_on_discharge_temperature_and_train_c_on_carryover(conn):
    a = trips.diagnose(conn, fid(conn, "20-K-2300A"), "2024-06-11T15:00", pack=PACK)
    assert a["value"] == "discharge_temperature" and a["first_out"]["point"].endswith("20-TT-2300A.PV")
    assert any("after-cooler" in c for c in a["checklist"])
    c = trips.diagnose(conn, fid(conn, "20-K-2100C"), "2024-11-05T05:30", pack=PACK)
    assert c["value"] == "liquid_carryover" and c["first_out"]["unit"] in ("20-V-2001", "20-V-2101C")
    assert {m["unit"] for m in c["sequence"]} >= {"20-V-2101C"}
    with pytest.raises(CalcError, match="did not trip"):
        trips.diagnose(conn, fid(conn, "20-K-2200B"), "2025-02-01T00:00", pack=PACK)


def test_trips_reach_the_ioc_with_the_restart_checklist(conn):
    from fuzer.ops import exceptions

    # The tests share the module's plant in random order: the machine health test's December 2025 watch leaves
    # train B's seal and hot section cases open, and a train B trip rightly joins an open case on its train. This
    # test follows the trip's own case, so it starts with none of those open.
    for r in conn.execute("select key from exception_record where type = 'machine_health' and state = 'open'"):
        exceptions.clear(conn, "machine_health", r["key"], "test", "not this test's")
    conn.commit()
    out = trips.scan(conn, PACK, "2024-03-13T00:00", "2024-03-15T00:00")
    assert out["trips"] == 1 and len(out["raised"]) == 1 and not out["errors"]
    row = conn.execute("select * from exception_record where type = 'machine_trip'").fetchone()
    found = row["evidence"]["finding"]
    assert said(row)["Likely cause"] == "high vibration" and found["checks"] and found["method"] == "compressor_trip"
    assert said(row)["First out"].endswith("20-VT-2200B.PV") and row["item_id"]
    again = trips.scan(conn, PACK, "2024-03-13T00:00", "2024-03-15T00:00")  # the same trip: no second exception
    assert again["raised"][0]["new"] is False
    exceptions.dispatch(conn, row["id"], "ioc-ann", "fred", "check it before the restart")
    conn.commit()
    job = next(j for j in exceptions.field_jobs(conn, "fred") if j["id"] == row["id"])
    assert job["checklist"] == found["checks"] == job["finding"]["checks"]
    read = {i["label"]: i for i in job["finding"]["items"]}
    named = read["Likely source"]  # the asset by its name, not its id
    assert named["kind"] == "asset" and named["text"] and not named["text"].startswith("fz-") and read["Likely cause"]["value"] == "high vibration"


def test_machine_health_raises_flagged_machines_and_clears_them_when_fixed(conn):
    march = rotating.watch(conn, "2024-03-05T00:00")
    flagged = {r["machine"]: r for r in march["raised"]}
    assert flagged["20-K-2200B"]["status"] == "act" and flagged["20-K-2200B"]["item_id"]
    row = conn.execute("select * from exception_record where type = 'machine_health' and key = '20-K-2200B' "
                       "and state = 'open'").fetchone()
    assert "vibration in zone D" in row["title"] and row["priority"] == 2.0
    assert row["evidence"]["status"] == "act" and "zone D" in said(row)["Recommended first"]
    case = conn.execute("select role from work_item where id = %s", (row["item_id"],)).fetchone()
    assert case["role"] == "engineer" or conn.execute(
        "select 1 from work_token where item_id = %s and role = 'engineer'", (row["item_id"],)).fetchone()
    may = rotating.watch(conn, "2024-05-01T00:00")  # bearings replaced on 3 April: only its efficiency to watch
    row = conn.execute("select * from exception_record where id = %s", (row["id"],)).fetchone()
    assert row["state"] == "open" and row["evidence"]["status"] == "watch" and "zone D" not in row["title"]
    assert row["priority"] == 0.5 and not any(r["machine"] == "20-K-2100A" for r in may["raised"])  # wear-out only
    dec = rotating.watch(conn, "2025-12-31T00:00")
    flagged = {r["machine"]: r for r in dec["raised"]}
    assert flagged["20-K-2100C"]["status"] == "act" and "20-K-2200B" in dec["cleared"]
    surge = conn.execute("select * from exception_record where type = 'machine_health' and key = '20-K-2100C' "
                         "and state = 'open'").fetchone()
    assert "near surge" in surge["title"] and "anti-surge" in said(surge)["Recommended first"]
    assert conn.execute("select state from exception_record where id = %s", (row["id"],)).fetchone()["state"] == "cleared"


def test_trip_causes_vibration_and_ambient_read_roles_not_descriptions_or_tag_names(conn):
    """P1.4c: with every point's description blanked and the tags renamed out of their -K-, -V- and -E- letters, the
    trip causes, the train's run signal, the vibration zones and the ambient point come out the same. They read the
    measurement roles and the equipment classes, so they run on any facility whose points carry roles."""
    ids = {ref: fid(conn, ref) for ref in ("20-K-2200B", "20-K-2300A", "20-K-2100C")}

    def seen() -> dict:
        b = trips.diagnose(conn, ids["20-K-2200B"], "2024-03-14T03:20", pack=PACK)
        a = trips.diagnose(conn, ids["20-K-2300A"], "2024-06-11T15:00", pack=PACK)
        c = trips.diagnose(conn, ids["20-K-2100C"], "2024-11-05T05:30", pack=PACK)
        v = rotating.vibration(conn, ids["20-K-2200B"], until="2024-03-05T00:00")
        return {"trips": [(d["value"], d["verdict"], d["tripped_at"], d["first_out"]["id"],
                           [(m["id"], m["family"]) for m in d["sequence"]]) for d in (b, a, c)],
                "vibration": (v["zone"], v["value"], v["point"]), "ambient": rotating._ambient_point(conn)["id"]}

    before = seen()
    try:
        conn.execute("update golden set value = '\"\"' where field = 'description' "
                     "and fuzer_id in (select fuzer_id from entity where kind = 'timeseries')")
        conn.execute("update entity_label set label = replace(label, '-', '_') "
                     "where fuzer_id in (select fuzer_id from entity where kind = 'tag')")
        assert seen() == before
    finally:
        conn.rollback()
