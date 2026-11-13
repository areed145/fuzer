"""Work that needs a permit at a site with no permit system loaded (#742, decided 13 Nov 2026: warn, don't block).
Okan's permit-to-work records are set aside for the test, as at a site with no permit system. With round findings
listed as needing a permit, the dispatch card says check permit instead of a plain ready, the phone asks for a permit in hand, and whether the operator confirmed one is kept on
the case. Nothing is refused, and a work type nobody listed is unchanged."""

from fuzer.core import settings
from fuzer.core.exceptions import raise_exception
from fuzer.ops import (
    exceptions,
    readiness,
    rounds,  # noqa: F401  (declares round_finding)
)


def _id(conn, label: str) -> str:
    return conn.execute("select fuzer_id from entity_label where label = %s", (label,)).fetchone()["fuzer_id"]


def _comments(conn, item_id: int) -> list[str]:
    return [r["body"] for r in conn.execute("select data->>'text' as body from work_event where item_id = %s and "
                                            "kind = 'commented' order by id", (item_id,))]


def test_a_permit_job_with_no_permit_records_warns_and_keeps_the_confirmation(okan_db):
    conn = okan_db.conn
    k = _id(conn, "20-K-2200B")
    conn.execute("update entity set kind = 'permit_set_aside' where kind = 'permit'")  # rolled back at the end
    e = raise_exception(conn, "round_finding", "K-2200B seal weeping", "Seal weeping at the drive end", "olga",
                        refs=[k])["id"]
    plain = readiness.permits(conn, [k], e)  # nobody listed round findings: as before
    assert plain["ready"] and not plain["needed"] and "warnings" not in plain
    settings.set(conn, "readiness.permit_types", "round_finding", by="test")
    try:
        r = readiness.check(conn, [k], e)
        p = next(c for c in r["checks"] if c["check"] == "permits")
        assert p["ready"] and p["confirm"] and p["needed"] and not p["checked"]  # warned, never held
        assert not any("permit" in b for b in r["blockers"])
        assert any(w.startswith("Check permit: this round finding work needs a permit, and Fuzer has no permit "
                                "records to check") for w in r["warnings"])
        exceptions.dispatch(conn, e, "ioc-ann", "fred", "change the seal", override="test: parts on site")
        [mine] = [j for j in exceptions.field_jobs(conn, "fred") if j["id"] == e]
        assert mine["permit"]["unchecked"] and not mine["permit"]["blocked"] and "confirm one is in hand" in mine["permit"]["note"]
        row = exceptions.report(conn, e, "fred", "on_site", permit_in_hand=True)
        assert row["evidence"]["field"][-1]["permit_in_hand"] is True
        assert "Permit in hand: confirmed on site (no permit records to check)" in _comments(conn, row["item_id"])
        e2 = raise_exception(conn, "round_finding", "K-2200B second look", "Seal weeping again", "olga", refs=[k])["id"]
        exceptions.dispatch(conn, e2, "ioc-ann", "fred", "look again", override="test: parts on site")
        row2 = exceptions.report(conn, e2, "fred", "on_site")  # not confirmed: still starts, and the case says so
        assert row2["evidence"]["field"][-1]["permit_in_hand"] is False
        assert "Permit in hand: not confirmed on site (no permit records to check)" in _comments(conn, row2["item_id"])
    finally:
        settings.reset(conn, "readiness.permit_types", by="test")
        conn.rollback()
