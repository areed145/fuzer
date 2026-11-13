"""Retention (R.5d): ended sessions, old access events and stale assistant conversations go by their settings, each
with a stated default, and the work's own history never does."""

from datetime import UTC, datetime, timedelta

from fuzer.core import retention, settings


def test_defaults_keep_the_audit_and_conversations_and_drop_only_ended_sessions(okan_db):
    conn = okan_db.conn
    assert settings.get(conn, "audit.retention_days") == 0 and settings.get(conn, "assistant.retention_days") == 0
    conn.execute("insert into access_account (id, tenant_id, object_id, display_name) values ('ret-test', 't', 'ret-test', "
                 "'Retention test') "
                 "on conflict do nothing")
    conn.execute("insert into access_session (token_hash, account_id, csrf, expires_at) values "
                 "('ret-ended', 'ret-test', 'x', now() - interval '1 hour'), "
                 "('ret-live', 'ret-test', 'x', now() + interval '1 hour')")
    conn.execute("insert into access_event (actor, action, at) values ('user:ret-test', 'signed_in', "
                 "now() - interval '4000 days')")
    conn.execute("insert into assistant_conversation (id, by_user, updated_at) values ('ret-old', 'user:ret-test', "
                 "now() - interval '4000 days')")
    conn.commit()

    out = retention.run(conn)
    assert out["sessions"] >= 1 and out["access_events"] == 0 and out["conversations"] == 0
    left = {r["token_hash"] for r in conn.execute("select token_hash from access_session where account_id = 'ret-test'")}
    assert left == {"ret-live"}
    assert conn.execute("select 1 from access_event where actor = 'user:ret-test'").fetchone()
    assert conn.execute("select 1 from assistant_conversation where id = 'ret-old'").fetchone()
    assert retention.policy(conn)["audit"] == "access events are kept indefinitely"


def test_set_periods_delete_what_is_past_them_and_say_so(okan_db):
    conn = okan_db.conn
    settings.set(conn, "audit.retention_days", 2555, by="test", reason="seven years, for the test")
    settings.set(conn, "assistant.retention_days", 365, by="test", reason="a year, for the test")
    now = datetime.now(UTC)
    conn.execute("insert into access_event (actor, action, at) values ('user:ret-old', 'signed_in', %s), "
                 "('user:ret-new', 'signed_in', %s)", (now - timedelta(days=2600), now - timedelta(days=10)))
    conn.execute("insert into assistant_conversation (id, by_user, updated_at) values ('ret-stale', 'u', %s), "
                 "('ret-fresh', 'u', %s)", (now - timedelta(days=400), now - timedelta(days=5)))
    work_events = conn.execute("select count(*) as n from work_event").fetchone()["n"]
    conn.commit()

    out = retention.run(conn, now)
    assert out["access_events"] >= 1 and out["conversations"] >= 1
    actors = {r["actor"] for r in conn.execute("select actor from access_event where actor like 'user:ret-%%'")}
    assert actors == {"user:ret-new"}
    kept = {r["id"] for r in conn.execute("select id from assistant_conversation where id like 'ret-%%'")}
    assert "ret-fresh" in kept and "ret-stale" not in kept
    assert conn.execute("select count(*) as n from work_event").fetchone()["n"] == work_events  # the work's history stays
    said = conn.execute("select data from access_event where action = 'retention_applied' order by id desc limit 1"
                        ).fetchone()["data"]
    assert said["access_events"] == out["access_events"] and said["audit_days"] == 2555
    assert retention.policy(conn)["audit"] == "access events are kept 2555 days"
    settings.set(conn, "audit.retention_days", 0, by="test", reason="back to the default")
    settings.set(conn, "assistant.retention_days", 0, by="test", reason="back to the default")
    conn.commit()
