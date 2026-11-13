"""How long Fuzer keeps what it records about people (R.5d, D9 in docs/ot-security.md): sign-in sessions, the access
log the audit record reads, and assistant conversations. Each is a setting with a stated default:

    access.session_hours       8      a session ends this long after sign-in; ended ones are deleted daily
    audit.retention_days       0      access events (sign-ins, permission changes, exports) older than this are
                                      deleted; 0 keeps every one
    assistant.retention_days   0      a conversation untouched this long goes, with its answers and images;
                                      0 keeps every one

The audit record's other sources are the work's own history (case events, write-backs, settings, source and model
changes): they stay as long as the work they belong to, so the audit retention never deletes them. `run` applies all
three in the project its connection is scoped to, and records what it deleted as an access event.
"""

from datetime import UTC, datetime, timedelta

from . import settings

SYSTEM = "system:retention"


def run(conn, now: datetime | None = None) -> dict:
    """Delete what is past its retention, commit, and say how much went (nothing recorded when nothing went)."""
    from ..agents.history import purge
    from . import access

    now = now or datetime.now(UTC)
    out = {"sessions": conn.execute("delete from access_session where expires_at <= %s", (now,)).rowcount
           + conn.execute("delete from access_login where expires_at <= %s", (now,)).rowcount,
           "access_events": 0, "conversations": 0}
    days = settings.get(conn, "audit.retention_days")
    if days > 0:
        out["access_events"] = conn.execute("delete from access_event where at < %s",
                                            (now - timedelta(days=days),)).rowcount
    conn.commit()
    out["conversations"] = purge(conn, settings.get(conn, "assistant.retention_days"), now)
    if out["access_events"] or out["conversations"]:
        access.event(conn, SYSTEM, "retention_applied", audit_days=days,
                     conversation_days=settings.get(conn, "assistant.retention_days"), **out)
        conn.commit()
    return out


def policy(conn) -> dict:
    """The retention in force, in words, for the audit export and the Settings page."""
    hours = settings.get(conn, "access.session_hours")
    audit, talk = settings.get(conn, "audit.retention_days"), settings.get(conn, "assistant.retention_days")
    return {"sessions": f"a session ends {hours} h after sign-in and is deleted once ended",
            "audit": f"access events are kept {audit} days" if audit else "access events are kept indefinitely",
            "conversations": (f"assistant conversations go {talk} days after their last message" if talk
                              else "assistant conversations are kept until their owner deletes them"),
            "work_history": "case, write-back, setting and source history stays as long as the work it belongs to"}
