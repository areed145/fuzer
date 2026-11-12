"""An open management of change on the job's equipment (Adam, 12 Nov, on #742): dispatch and the phone say so, and it
never holds the job. Okan's MOC register proposes a re-rate of the HP separator that nobody has approved."""

from fuzer.ops import readiness


def _id(conn, label: str) -> str:
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def test_an_open_moc_on_the_equipment_warns_and_never_blocks(okan_db):
    conn = okan_db.conn
    sep = _id(conn, "10-V-2001")
    r = readiness.check(conn, [sep])
    moc = next(c for c in r["checks"] if c["check"] == "moc")
    assert moc["ready"] and moc["blockers"] == []
    assert [m["moc"] for m in moc["mocs"]] == ["MOC-2025-031"]  # the closed ones are history, not a warning
    assert r["warnings"] == ["Open MOC MOC-2025-031 on 10-V-2001: Proposed re-rate with new internals. "
                             "Not approved; no change to operating limits"]
    assert not any("MOC" in b for b in r["blockers"])
    conn.rollback()


def test_no_open_moc_says_nothing(okan_db):
    conn = okan_db.conn
    other = conn.execute("select fuzer_id from entity where kind = 'tag' and fuzer_id not in "
                         "(select fuzer_id from measurement where method like 'moc%%') limit 1").fetchone()["fuzer_id"]
    r = readiness.check(conn, [other])
    assert r["warnings"] == [] and next(c for c in r["checks"] if c["check"] == "moc")["mocs"] == []
    conn.rollback()
