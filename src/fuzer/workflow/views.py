"""Saved views per role on All work (P5.1c, #651): a named set of All work's filters, kept for a role, so everyone in
that role finds the same view in All work's picker. Only someone in the role saves or removes a view for it."""

from psycopg.types.json import Jsonb

from .definitions import roles_of

LISTS = {"dispatch", "field", "back", "open", "progress", "ranked", "closed"}
FILTERS = ("type", "who", "q")


def views(conn, person: str) -> list[dict]:
    """The views saved for any role the person holds, by role then name."""
    return conn.execute(
        "select id, role, name, list, filters, created_by, created_at from work_view where role = any(%s) "
        "order by role, lower(name)", (roles_of(conn, person),)).fetchall()


def save_view(conn, person: str, role: str, name: str, list_: str, filters: dict | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("name the view")
    if role not in roles_of(conn, person):
        raise PermissionError(f"{person} is not a {role.replace('_', ' ')}, so cannot save a view for that role")
    if list_ not in LISTS:
        raise ValueError(f"no All work view called {list_}")
    kept = {k: str(v) for k, v in (filters or {}).items() if k in FILTERS and v}
    return conn.execute(
        "insert into work_view (role, name, list, filters, created_by) values (%s, %s, %s, %s, %s) "
        "on conflict (role, name) do update set list = excluded.list, filters = excluded.filters, "
        "created_by = excluded.created_by, created_at = now() "
        "returning id, role, name, list, filters, created_by, created_at",
        (role, name, list_, Jsonb(kept), person)).fetchone()


def remove_view(conn, person: str, view_id: int) -> None:
    row = conn.execute("select role from work_view where id = %s", (view_id,)).fetchone()
    if not row:
        raise LookupError(f"no saved view {view_id}")
    if row["role"] not in roles_of(conn, person):
        raise PermissionError(f"only a {row['role'].replace('_', ' ')} removes this view")
    conn.execute("delete from work_view where id = %s", (view_id,))

