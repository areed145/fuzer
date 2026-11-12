"""Home is my shift (P5.1): what changed on a person's work since they last looked at Home. Their work is every case
they hold or opened, every case in their roles' queues, and every case they watch (P5.1b); a change is anything
someone else did on it (a hand-off, an escalation, a comment, a close, a gate stop, a step that failed)."""

from .definitions import roles_of

CHANGES = ("created", "handed_off", "escalated", "commented", "state_changed", "completed_process", "cancelled",
           "gated", "failed", "agent_failed", "delegated", "item_changed")


def seen(conn, person: str) -> str | None:
    row = conn.execute("select seen_at from work_home_seen where name = %s", (person,)).fetchone()
    return row["seen_at"].isoformat() if row else None


def mark_seen(conn, person: str) -> str:
    row = conn.execute("insert into work_home_seen (name) values (%s) on conflict (name) do update set seen_at = now() "
                       "returning seen_at", (person,)).fetchone()
    return row["seen_at"].isoformat()


def changes(conn, person: str, since: str | None = None, limit: int = 30) -> list[dict]:
    """What others did on the person's work since `since` (else the last day), newest first, one line per event."""
    roles = roles_of(conn, person)
    return conn.execute(
        """
        select e.id, e.item_id, e.kind, e.actor, e.at, e.data, i.title, i.type, i.state,
               not coalesce(i.assignee = %(p)s or i.created_by = %(p)s or i.role = any(%(roles)s)
                    or i.role = any(select 'role:' || r from unnest(%(roles)s::text[]) r), false) as watching
        from work_event e join work_item i on i.id = e.item_id
        where e.at > coalesce(%(since)s::timestamptz, now() - interval '1 day') and e.actor <> %(p)s
          and e.kind = any(%(kinds)s)
          and (i.assignee = %(p)s or i.created_by = %(p)s or i.role = any(%(roles)s)
               or i.role = any(select 'role:' || r from unnest(%(roles)s::text[]) r)
               or exists (select 1 from work_watch w where w.name = %(p)s
                          and (w.ref = 'case:' || i.id or w.ref = any(i.subjects))))
        order by e.id desc limit %(n)s
        """, {"since": since, "p": person, "kinds": list(CHANGES), "roles": roles, "n": limit}).fetchall()


def _ref(ref: str) -> str:
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("say what to watch: an asset id or case:<id>")
    return ref


def watch(conn, person: str, ref: str, on: bool = True) -> dict:
    """Watch an asset (its fuzer id) or a case (`case:<id>`), or stop. A watch shows its new cases and changes on the
    watcher's Home under Watching; it never assigns the work to them."""
    ref = _ref(ref)
    if on:
        conn.execute("insert into work_watch (name, ref) values (%s, %s) on conflict do nothing", (person, ref))
    else:
        conn.execute("delete from work_watch where name = %s and ref = %s", (person, ref))
    return {"person": person, "ref": ref, "watching": on}


def watching(conn, person: str) -> list[str]:
    return [r["ref"] for r in conn.execute(
        "select ref from work_watch where name = %s order by created_at", (person,)).fetchall()]


def home(conn, person: str, mark: bool = False) -> dict:
    """The person's Home since they last looked; `mark` records this visit as the new last look."""
    since = seen(conn, person)
    out = {"person": person, "seen_at": since, "changes": changes(conn, person, since),
           "watching": watching(conn, person)}
    if mark:
        mark_seen(conn, person)
    return out
