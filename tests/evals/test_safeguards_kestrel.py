"""Safeguards and bypasses (P3.9a, #763) on Kestrel: the safeguard and bypass registers load as a source, a bypass
still in place past its expiry is work for the ops lead, and it clears when the bypass comes out. What a bypass
takes away is read from the registers and the piping, never from tag names. Its own module, as Kestrel's detectors
read Kestrel's model."""

from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from fuzer.core import detectors
from fuzer.core.pack import resolve
from fuzer.evals import loop
from fuzer.ops import safeguards
from fuzer.workflow import engine


def _fid(conn, label: str) -> str:
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def test_the_registers_say_what_is_bypassed_and_on_what(kestrel_db):
    conn = kestrel_db
    live = {b["label"]: b for b in safeguards.bypasses(conn)}
    assert set(live) == {"BYP-KR-031", "BYP-KR-032"}  # BYP-KR-029 was removed weeks ago
    scrubber = live["BYP-KR-031"]
    assert scrubber["overdue"] and scrubber["overdue_hours"] > 0 and not scrubber["moc"]
    assert scrubber["protects_labels"] == ["B1-V-2005"] and scrubber["acts_on_labels"] == ["B1-LT-2005"]
    assert "no change record" in scrubber["say"] and "under MOC-KR-0142" in live["BYP-KR-032"]["say"]
    assert not live["BYP-KR-032"]["overdue"]

    k = _fid(conn, "B1-K-2001")
    assert [b["label"] for b in safeguards.bypasses(conn, [k])] == []  # nothing on the compressor itself
    # its suction scrubber's trip is bypassed: the same train, by its piping
    assert [b["label"] for b in safeguards.bypasses(conn, [k], system=True)] == ["BYP-KR-031"]
    assert [b["label"] for b in safeguards.bypasses(conn, [_fid(conn, "C1-H-3008")])] == ["BYP-KR-032"]
    assert len(safeguards.bypasses(conn, in_place=False)) == 3


def test_an_overdue_bypass_is_work_for_the_ops_lead_until_it_is_removed(kestrel_db):
    conn = kestrel_db
    engine.load_defaults(conn)
    loop._people(conn)
    conn.commit()
    out = detectors.run(conn, resolve(conn), "bypass_overdue")
    assert [r["bypass"] for r in out["raised"]] == ["BYP-KR-031"] and not out["errors"], out
    x = conn.execute("select * from exception_record where type = 'bypass_overdue' and state = 'open'").fetchone()
    assert x["key"] == "BYP-KR-031" and x["item_id"] and "B1-V-2005" in x["title"]
    role = conn.execute("select role from work_item where id = %s", (x["item_id"],)).fetchone()["role"]
    assert role == "ops_lead"
    assert detectors.run(conn, resolve(conn), "bypass_overdue")["raised"][0]["new"] is False  # raised once

    byp = _fid(conn, "BYP-KR-031")
    conn.execute("update golden set value = to_jsonb('removed'::text) where field = 'state' and fuzer_id = %s", (byp,))
    try:
        out = detectors.run(conn, resolve(conn), "bypass_overdue")
        assert out["cleared"] == ["BYP-KR-031"] and not out["raised"]
        assert conn.execute("select state from exception_record where id = %s", (x["id"],)).fetchone()["state"] != "open"
    finally:  # back in place, for the other tests on this database
        conn.execute("update golden set value = to_jsonb('active'::text) where field = 'state' and fuzer_id = %s", (byp,))
        conn.commit()


def test_a_case_on_the_system_leads_its_evidence_with_the_bypass_and_the_open_changes(kestrel_db):
    """P3.9b: a case on the compressor shows its scrubber's trip bypassed (same train, by the piping) and the open
    alarm change on its discharge temperature; one on the reboiler shows its shutdown bypassed under its change."""
    from fuzer.api.workflow import work_item_safeguards  # what the case page reads
    from fuzer.core.exceptions import raise_exception

    conn = kestrel_db
    engine.load_defaults(conn)
    loop._people(conn)
    conn.commit()
    k = raise_exception(conn, "records_gap", "test-k", "Compressor question", "test", refs=[_fid(conn, "B1-K-2001")])
    r = raise_exception(conn, "records_gap", "test-r", "Reboiler question", "test", refs=[_fid(conn, "C1-H-3008")])
    conn.commit()
    on_k = safeguards.on_case(conn, k["item_id"])
    assert [b["label"] for b in on_k["bypasses"]] == ["BYP-KR-031"] and on_k["bypasses"][0]["overdue"]
    assert [c["label"] for c in on_k["changes"]] == ["MOC-KR-0138"]  # MOC-KR-0117 on the inlet is closed and elsewhere
    got = work_item_safeguards(r["item_id"], conn=conn)
    assert [b["label"] for b in got["bypasses"]] == ["BYP-KR-032"] and got["bypasses"][0]["moc"] == "MOC-KR-0142"
    assert [c["label"] for c in got["changes"]] == ["MOC-KR-0142"]


def test_a_limit_changed_with_no_change_record_is_a_question_until_one_turns_up(kestrel_db):
    """P3.9c (#343): the scrubber's high-high level alarm changed three days ago with no change record; the
    discharge temperature high alarm changed under MOC-KR-0138, so it is not raised. Only the latest change to each
    limit is in the export, so only that one is claimed."""
    conn = kestrel_db
    engine.load_defaults(conn)
    loop._people(conn)
    conn.commit()
    out = detectors.run(conn, resolve(conn), "unrecorded_changes")
    assert not out["errors"], out
    x = conn.execute("select * from exception_record where type = 'unrecorded_change' and state = 'open'").fetchall()
    assert [r["title"].split(" changed")[0] for r in x] == ["B1-LT-2005 high-high alarm"], [r["title"] for r in x]
    assert x[0]["item_id"]

    tag = _fid(conn, "B1-LT-2005")  # the record turns up: a change about the transmitter, opened the day before
    conn.execute("insert into entity (fuzer_id, kind) values ('fz-change-test', 'change')")
    conn.execute("insert into entity_label (fuzer_id, label) values ('fz-change-test', 'MOC-KR-0150')")
    conn.execute("insert into edge (src, rel, dst, sources, confidence) values ('fz-change-test', 'concerns', %s, "
                 "'{test}', 1)", (tag,))
    opened = conn.execute("select (pl.valid_from - interval '1 day')::text as t from point_limit pl join edge h "
                          "on h.rel = 'has_timeseries' and h.dst = pl.point_id where h.src = %s and pl.limit_type = "
                          "'hihi' and pl.valid_from > now() - interval '30 days'", (tag,)).fetchone()["t"]
    conn.execute("insert into golden (fuzer_id, field, value, source, rule) values ('fz-change-test', 'opened', "
                 "to_jsonb(%s::text), 'test', 'test'), ('fz-change-test', 'state', to_jsonb('approved'::text), 'test', "
                 "'test')", (opened,))
    try:
        out = detectors.run(conn, resolve(conn), "unrecorded_changes")
        assert out["cleared"] == [x[0]["key"]] and not out["raised"], out
    finally:  # gone again, for the other tests on this database
        for t in ("golden", "edge", "entity_label", "entity"):
            conn.execute(f"delete from {t} where {'src' if t == 'edge' else 'fuzer_id'} = 'fz-change-test'")
        conn.commit()


def test_two_jobs_on_a_system_with_a_bypassed_safeguard_clash(kestrel_db):
    """P5.11b with P3.9 (#707): two jobs at once on the same system while one of its safeguards is bypassed are a
    simultaneous-operations conflict, said with the bypass; the system comes from the piping, not the tag names."""
    from fuzer.ops import simops

    conn = kestrel_db
    compressor = simops.job(conn, "Replace K-2001 valve", [_fid(conn, "B1-K-2001")])
    scrubber = simops.job(conn, "Inspect scrubber level bridle", [_fid(conn, "B1-V-2005")])
    said = [c for c in simops.conflicts(conn, compressor, scrubber, same_asset=False) if c["kind"] == "bypass"]
    assert [c["bypass"] for c in said] == ["BYP-KR-031"]
    assert said[0]["say"].startswith("“Inspect scrubber level bridle” is on the same system, where a safeguard is "
                                     "bypassed: ") and "BYP-KR-031" in said[0]["say"]
    # a job elsewhere shares no bypass with it
    contactor = simops.job(conn, "Contactor manway", [_fid(conn, "C1-V-3001")])
    assert not [c for c in simops.conflicts(conn, compressor, contactor) if c["kind"] == "bypass"]
    # the reboiler's own bypass (BYP-KR-032) doesn't reach the contactor's system either
    reboiler = simops.job(conn, "Reboiler burner", [_fid(conn, "C1-H-3008")])
    assert [b["label"] for b in reboiler["bypasses"]] == ["BYP-KR-032"]
    assert not [c for c in simops.conflicts(conn, reboiler, contactor) if c["kind"] == "bypass"]


def test_a_safeguard_past_its_proof_test_interval_is_work_until_it_is_tested(kestrel_db):
    """P4.2e (#850): the suction scrubber's trip was last tested 400 days ago on a 12 month interval, and the reboiler
    shutdown has no test on file; both are work for the engineer, and a test on file clears one."""
    conn = kestrel_db
    tests = {p["label"]: p for p in safeguards.proof_tests(conn)}
    assert set(tests) == {f"SG-KR-00{i}" for i in range(1, 6)}
    scrubber, reboiler = tests["SG-KR-003"], tests["SG-KR-005"]
    assert scrubber["overdue"] and 30 <= scrubber["overdue_days"] <= 40 and scrubber["protects_labels"] == ["B1-V-2005"]
    assert reboiler["overdue"] and reboiler["due"] is None and reboiler["overdue_days"] is None
    assert not tests["SG-KR-002"]["overdue"]  # 500 days on a 24 month interval
    k = _fid(conn, "B1-K-2001")
    assert [p["label"] for p in safeguards.proof_tests(conn, [k])] == ["SG-KR-002", "SG-KR-004"]

    engine.load_defaults(conn)
    loop._people(conn)
    conn.commit()
    out = detectors.run(conn, resolve(conn), "proof_test_overdue")
    assert sorted(r["safeguard"] for r in out["raised"]) == ["SG-KR-003", "SG-KR-005"] and not out["errors"], out
    x = conn.execute("select * from exception_record where type = 'proof_test_overdue' and key = 'SG-KR-005'").fetchone()
    assert "no proof test on file" in x["title"] and "C1-H-3008" in x["title"]
    assert conn.execute("select role from work_item where id = %s", (x["item_id"],)).fetchone()["role"] == "engineer"

    sg = _fid(conn, "SG-KR-005")
    was = conn.execute("select * from golden where fuzer_id = %s and field = 'last_proof_test'", (sg,)).fetchone()
    conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, 'last_proof_test', "
                 "to_jsonb(%s::text), 'test', 'test') on conflict (fuzer_id, field) do update set value = excluded.value",
                 (sg, datetime.now(UTC).date().isoformat()))
    try:
        out = detectors.run(conn, resolve(conn), "proof_test_overdue")
        assert out["cleared"] == ["SG-KR-005"] and [r["safeguard"] for r in out["raised"]] == ["SG-KR-003"]
    finally:  # untested again, for the other tests on this database
        conn.execute("delete from golden where fuzer_id = %s and field = 'last_proof_test'", (sg,))
        if was:
            conn.execute("insert into golden values (%(fuzer_id)s, %(field)s, %(value)s, %(source)s, %(rule)s)",
                         {**was, "value": Jsonb(was["value"])})
        conn.commit()


def test_a_safeguard_demanded_more_than_designed_is_work_for_the_engineer(kestrel_db):
    """P4.2e (#850): the compressor vibration trip was called on twice this year (96 days ago and yesterday's trip),
    designed for 0.1 a year; the probe fault 60 days ago was spurious, so it is listed but not counted. The discharge
    temperature trip's one demand is within what one year allows. A failed demand is raised on its own."""
    conn = kestrel_db
    got = {d["label"]: d for d in safeguards.demands(conn)}
    assert set(got) == {"SG-KR-002", "SG-KR-004"}
    vib = got["SG-KR-002"]
    assert [e["demand"] for e in vib["demands"]] == ["DEM-KR-010", "DEM-KR-007"] and vib["over"]
    assert [e["demand"] for e in vib["spurious"]] == ["DEM-KR-008"] and vib["designed_per_year"] == 0.1
    assert not got["SG-KR-004"]["over"] and not got["SG-KR-004"]["failed"]
    assert safeguards.demands(conn, [_fid(conn, "C1-H-3008")]) == []

    engine.load_defaults(conn)
    loop._people(conn)
    conn.commit()
    out = detectors.run(conn, resolve(conn), "safeguard_demands")
    assert [r["safeguard"] for r in out["raised"]] == ["SG-KR-002"] and not out["errors"], out
    x = conn.execute("select * from exception_record where type = 'safeguard_demand' and key = 'SG-KR-002'").fetchone()
    assert "demanded 2 times in 365 days" in x["title"] and "B1-K-2001" in x["title"]
    assert conn.execute("select role from work_item where id = %s", (x["item_id"],)).fetchone()["role"] == "engineer"

    dem = _fid(conn, "DEM-KR-009")
    conn.execute("update golden set value = to_jsonb('failed'::text) where fuzer_id = %s and field = 'outcome'", (dem,))
    try:
        out = detectors.run(conn, resolve(conn), "safeguard_demands")
        assert sorted(r["safeguard"] for r in out["raised"]) == ["SG-KR-002", "SG-KR-004"]
        t = conn.execute("select title from exception_record where type = 'safeguard_demand' and key = 'SG-KR-004'")
        assert "failed to act on a demand" in t.fetchone()["title"]
    finally:  # it worked, for the other tests on this database
        conn.execute("update golden set value = to_jsonb('worked'::text) where fuzer_id = %s and field = 'outcome'",
                     (dem,))
        conn.commit()


def test_the_specialist_sees_all_three_checks_on_an_item(kestrel_db):
    """P4.2e: the safety systems pack's tool on the compressor: its scrubber's trip bypassed (same train) and overdue
    for its test, and its vibration trip demanded more than designed."""
    from fuzer.agents.capabilities.operations import safeguard_status

    got = safeguard_status("B1-K-2001")
    assert [b["label"] for b in got["bypasses"]] == ["BYP-KR-031"]
    assert [p["label"] for p in got["proof_tests_overdue"]] == ["SG-KR-003"]
    assert [(d["label"], d["over"]) for d in got["demands"]] == [("SG-KR-002", True), ("SG-KR-004", False)]
    assert len(safeguard_status()["proof_tests_overdue"]) == 2 and "error" in safeguard_status("no-such-thing")


def test_a_temporary_moc_past_its_end_date_is_work_until_it_closes(kestrel_db):
    """#755: the change records say which changes are temporary, whether the pre-startup review is done, who owns them
    and the work order carrying them out. The temporary clamp-on meter on the lean amine circulation is five days past
    its end date: work for the engineer, with its owner, until the change closes. Dispatch and the phone warn about a
    change implemented before its review, and never hold the job."""
    from fuzer.ops import readiness

    conn = kestrel_db
    by = {c["label"]: c for c in safeguards.changes(conn)}
    assert set(by) == {"MOC-KR-0129", "MOC-KR-0138", "MOC-KR-0142", "MOC-KR-0151"}  # MOC-KR-0117 is closed
    meter, rerange = by["MOC-KR-0129"], by["MOC-KR-0151"]
    assert safeguards.stage(meter)["expired"] and 4 <= safeguards.stage(meter)["overdue_days"] <= 6
    assert not safeguards.stage(by["MOC-KR-0142"])["expired"]  # temporary, but not due for two days
    assert safeguards.stage(rerange)["pssr_pending"] and rerange["work_orders"] == ["40007002"]
    assert rerange["owner"] == "Instrument Engineer" and rerange["change_class"] == "permanent"

    r = readiness.check(conn, [_fid(conn, "A1-LT-1001")])
    moc = next(c for c in r["checks"] if c["check"] == "moc")
    assert moc["ready"] and not any("MOC" in b for b in r["blockers"])
    assert [m["moc"] for m in moc["mocs"]] == ["MOC-KR-0151"] and moc["mocs"][0]["pssr_pending"]
    assert any(w.startswith("MOC MOC-KR-0151 on A1-LT-1001 is implemented but its pre-startup safety review is not "
                            "done") and w.endswith("Carried out by work order 40007002") for w in r["warnings"])
    w = readiness.mocs(conn, [_fid(conn, "D1-FT-4009")])["warnings"]
    assert len(w) == 1 and w[0].startswith("Temporary MOC MOC-KR-0129 on D1-FT-4009 is past its end date")

    engine.load_defaults(conn)
    loop._people(conn)
    conn.commit()
    out = detectors.run(conn, resolve(conn), "temporary_moc_overdue")
    assert [r["change"] for r in out["raised"]] == ["MOC-KR-0129"] and not out["errors"], out
    x = conn.execute("select * from exception_record where type = 'temporary_moc_overdue' and state = 'open'").fetchone()
    assert x["key"] == "MOC-KR-0129" and "D1-FT-4009" in x["title"] and "days past its end date" in x["title"]
    assert conn.execute("select role from work_item where id = %s", (x["item_id"],)).fetchone()["role"] == "engineer"

    fid = _fid(conn, "MOC-KR-0129")
    conn.execute("update golden set value = to_jsonb('closed'::text) where field = 'state' and fuzer_id = %s", (fid,))
    try:
        out = detectors.run(conn, resolve(conn), "temporary_moc_overdue")
        assert out["cleared"] == ["MOC-KR-0129"] and not out["raised"]
    finally:  # open again, for the other tests on this database
        conn.execute("update golden set value = to_jsonb('implemented'::text) where field = 'state' and fuzer_id = %s",
                     (fid,))
        conn.commit()
