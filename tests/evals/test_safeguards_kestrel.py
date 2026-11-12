"""Safeguards and bypasses (P3.9a, #763) on Kestrel: the safeguard and bypass registers load as a source, a bypass
still in place past its expiry is work for the ops lead, and it clears when the bypass comes out. What a bypass
takes away is read from the registers and the piping, never from tag names. Its own module, as Kestrel's detectors
read Kestrel's model."""

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
