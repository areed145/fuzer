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


def test_many_jobs_at_once_read_the_same_as_one_at_a_time(okan_db):
    """My work looks every job up together: each job's permits and MOC warnings match the one-job checks."""
    conn = okan_db.conn
    tags = [r["fuzer_id"] for r in conn.execute(
        "select fuzer_id from entity where kind = 'tag' order by fuzer_id limit 40")]
    subjects = {1: [_id(conn, "10-V-2001")], 2: tags[:3], 3: [], 4: tags[3:]}
    places = readiness.locations_many(conn, subjects)
    assert {j: sorted(p) for j, p in places.items()} == {
        j: sorted(readiness._locations(conn, s)) for j, s in subjects.items()}
    many = readiness.mocs_many(conn, places)
    assert many == {j: readiness.mocs(conn, s) for j, s in subjects.items()} and many[1]["warnings"]
    # A type that needs no permit reads exactly as a check with no job does.
    now = readiness.datetime.now(readiness.UTC)
    assert readiness.permits_many(conn, {j: ("not_permit_work", s) for j, s in subjects.items()}, places, now) == {
        j: readiness.permits(conn, s, None, now) for j, s in subjects.items()}
    conn.rollback()

