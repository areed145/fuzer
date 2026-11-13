"""The project's audit record (R.5): every actor column is read or excluded with a reason, per project."""

from datetime import UTC, datetime, timedelta

import pytest

from fuzer.core import access, audit


def test_every_actor_column_is_in_the_record_or_excluded_with_a_reason(okan_db):
    cov = audit.coverage(okan_db.conn)
    # A new table naming who did something joins the record by itself, unless its time column can't be found:
    # then name it in audit.TIMES, or exclude it in audit.EXCLUDED with why.
    assert cov["unpaired"] == []
    # A shared table (schema fuzer) holds every project's rows: it needs a project filter in audit._filter.
    assert cov["unfiltered"] == []
    included = {c["source"] for c in cov["included"]}
    assert {"work_event.actor", "writeback.approved_by", "writeback_event.by", "access_event.actor",
            "gate_decision.actor", "setting_history.by", "source_audit.by"} <= included
    assert all(e["reason"] for e in cov["excluded"])
    assert not included & {e["source"] for e in cov["excluded"]}
    assert not any(s.startswith("assistant_") for s in included)  # private conversations stay private


def test_record_is_windowed_ordered_and_says_which_sources_keep_every_change(okan_db):
    conn = okan_db.conn
    now = datetime.now(UTC)
    before = audit.record(conn, "okan", now - timedelta(days=3650), now + timedelta(minutes=1), limit=100000)
    assert before, "the Okan build records who loaded, approved and routed things"
    assert [r["at"] for r in before] == sorted(r["at"] for r in before)
    access.event(conn, "user:auditor-test", "role_changed_for_test", project="okan")
    conn.commit()
    after = audit.record(conn, "okan", now, datetime.now(UTC) + timedelta(minutes=1), limit=100000)
    mine = [r for r in after if r["actor"] == "user:auditor-test"]
    assert len(mine) == 1 and mine[0]["history"] and mine[0]["source"] == "access_event.actor"
    assert mine[0]["action"] == "role_changed_for_test" and mine[0]["project"] == "okan"
    latest_only = {r["source"] for r in before if not r["history"]}
    assert "work_item.created_by" in latest_only
    assert "work_event.actor" not in latest_only
    csv = audit.to_csv(after)
    assert csv.splitlines()[0] == ",".join(audit.FIELDS) and "every change" in csv


def test_bad_windows_and_oversized_exports_are_refused(okan_db):
    now = datetime.now(UTC)
    with pytest.raises(audit.AuditError, match="before its end"):
        audit.record(okan_db.conn, "okan", now, now)
    with pytest.raises(audit.AuditError, match="shorter window"):
        audit.record(okan_db.conn, "okan", now - timedelta(days=3650), now + timedelta(minutes=1), limit=1)


def test_cli_exports_a_window_and_records_the_export(okan_db, tmp_path):
    from fuzer.cli import main
    from fuzer.core import db

    out = tmp_path / "audit.csv"
    before = db.scope()
    try:
        main(["audit", "export", "--since", "2000-01-01", "--out", str(out), "--by", "auditor"])
    finally:
        db.set_default(before)  # the CLI makes its project the process's; later tests expect none
    assert out.read_text().startswith(",".join(audit.FIELDS))
    row = okan_db.conn.execute("select actor, project, data from access_event where action = 'audit_exported' "
                               "order by id desc limit 1").fetchone()
    assert row["actor"] == "cli:auditor" and row["project"] == "okan" and row["data"]["format"] == "csv"
