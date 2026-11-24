"""Custom reports (docs/reports.md, "Custom reports"): the same definition as a standard report, kept in the database
per account, made by copying a report or starting blank and changed on the page itself. Each save is a new version
and the old ones are kept, so a report can go back to any of them. A copy keeps the report it came from and that
report's version, so a later change to it can be offered, never forced.

A custom report's key is `custom-<id>`; everything that reads a report (the page, its panels, the commentary) reads
it through `definition`, so a custom report is drawn by the same engine with the same checks.

Who sees one: its owner, and, once shared, everyone or the people who hold one workflow role. Only its owner
changes it. Sharing beyond yourself needs configure access (the API checks that; this module records it).

    definition(conn, pack, key)                  any report's checked definition, standard or custom
    listing(conn, actor, roles)                  the custom reports a person can open, each with mine / shared
    get(conn, key) / readable(row, actor, roles)
    copy(conn, pack, key, actor, title=None)     a new report from any report, keeping its origin
    blank(conn, actor, title, kinds=None)        a new report with no panels yet
    save(conn, key, definition, actor, base)     a new version; `base` is the version it was edited from
    share(conn, key, shared, actor)              '' (private), 'everyone' or 'role:<name>'
    versions(conn, key) / restore(conn, key, version, actor) / remove(conn, key, actor)
    parse(text)                                  a definition from YAML or JSON text (JSON is YAML)
    dump(definition)                             the definition as YAML text, in the order a person reads it
"""

import yaml
from psycopg.types.json import Jsonb

from ..core import reports
from ..core.reports import ReportError

PREFIX = reports.CUSTOM_PREFIX
EVERYONE = "everyone"
ROLE = "role:"
MAX_TEXT = 200_000
DEFAULT_KINDS = ("facility", "area")
# The fields a person writes; the rest (`key`, `version`, `source`) the store keeps.
OWN = ("key", "version", "source")


class NotYours(PermissionError):
    """A custom report someone else owns: only its owner changes it."""


class Stale(ValueError):
    """A save edited from a version that is no longer the latest."""


def key_of(rid: int) -> str:
    return f"{PREFIX}{rid}"


def _id(key: str) -> int | None:
    rest = key[len(PREFIX):] if key.startswith(PREFIX) else ""
    return int(rest) if rest.isdigit() else None


def is_custom(key: str) -> bool:
    return _id(key) is not None


def _row(conn, key: str) -> dict:
    rid = _id(key)
    row = rid and conn.execute("select * from custom_report where id = %s and deleted_at is null", (rid,)).fetchone()
    if not row:
        raise ReportError(f"no report {key}")
    return dict(row)


def get(conn, key: str) -> dict:
    """The stored report: its row, with the definition as the engine reads it."""
    return _row(conn, key)


def _checked(row: dict) -> dict:
    d = reports.check({**row["definition"], "key": key_of(row["id"]), "version": row["version"]}, "custom")
    return d


def definition(conn, pack, key: str) -> dict:
    """Any report's checked definition: a custom one from the database, else the platform's or the pack's."""
    if not is_custom(key):
        return reports.get(pack, key)
    return _checked(_row(conn, key))


def readable(row: dict, actor: str, roles) -> bool:
    shared = row.get("shared") or ""
    return (row["owner"] == actor or shared == EVERYONE
            or (shared.startswith(ROLE) and shared[len(ROLE):] in set(roles or ())))


def _out(row: dict, actor: str, pack=None) -> dict:
    d = row["definition"]
    origin = None
    if row.get("origin"):
        try:
            o = reports.get(pack, row["origin"])
            origin = {"key": o["key"], "title": o["title"], "version": o["version"],
                      "copied_version": row["origin_version"]}
        except ReportError:  # copied from a custom report, or one the pack no longer has
            origin = {"key": row["origin"], "title": None, "version": None, "copied_version": row["origin_version"]}
    return {"key": key_of(row["id"]), "title": row["title"], "for": list(d.get("for") or []),
            "scope": {"kinds": list((d.get("scope") or {}).get("kinds") or [])},
            "window": {"default": (d.get("window") or {}).get("default") or "7d"}, "source": "custom",
            "version": row["version"], "owner": row["owner"], "mine": row["owner"] == actor,
            "shared": row.get("shared") or "", "origin": origin,
            "updated_at": row["updated_at"], "updated_by": row["updated_by"]}


def listing(conn, actor: str, roles, pack=None) -> list[dict]:
    """The custom reports a person can open: their own, then those shared with everyone or one of their roles."""
    rows = conn.execute("select * from custom_report where deleted_at is null and (owner = %s or shared <> '') "
                        "order by title, id", (actor,)).fetchall()
    return [_out(dict(r), actor, pack) for r in rows if readable(dict(r), actor, roles)]


def about(conn, key: str, actor: str, pack=None) -> dict:
    """What the report page shows about a custom report beside its definition: owner, sharing, origin."""
    return _out(_row(conn, key), actor, pack)


def _plain(d: dict) -> dict:
    """What is stored: the definition without what the store keeps itself."""
    return {k: v for k, v in d.items() if k not in OWN}


def _keep(conn, rid: int, version: int, d: dict, actor: str, note: str) -> None:
    conn.execute("insert into custom_report_version (report_id, version, definition, by_user, note) "
                 "values (%s, %s, %s, %s, %s)", (rid, version, Jsonb(d), actor, note))


def _create(conn, actor: str, d: dict, origin: str | None = None, origin_version: int | None = None,
            note: str = "made") -> dict:
    row = conn.execute(
        "insert into custom_report (owner, title, definition, origin, origin_version, updated_by) "
        "values (%s, %s, '{}', %s, %s, %s) returning id", (actor, d["title"], origin, origin_version, actor)).fetchone()
    rid = row["id"]
    stored = _plain(reports.check({**d, "key": key_of(rid), "version": 1}, "custom"))
    conn.execute("update custom_report set definition = %s where id = %s", (Jsonb(stored), rid))
    _keep(conn, rid, 1, stored, actor, note)
    conn.commit()
    return _row(conn, key_of(rid))


def copy(conn, pack, key: str, actor: str, title: str | None = None) -> dict:
    """A new custom report, owned by `actor` and private, from any report they can open (the API checks that)."""
    src = definition(conn, pack, key)
    title = (title or "").strip() or f"{src['title']} (copy)"
    return _create(conn, actor, {**_plain(src), "title": title[:120]}, origin=src["key"],
                   origin_version=src["version"], note=f"copied from {src['title']} version {src['version']}")


def blank(conn, actor: str, title: str, kinds=None, window: str = "7d") -> dict:
    """A new custom report with no panels yet, for a scope of `kinds` (facilities and areas when none are named)."""
    title = (title or "").strip()
    if not title:
        raise ReportError("a report needs a title")
    return _create(conn, actor, {"schema": reports.SCHEMA, "title": title[:120], "for": [],
                                 "scope": {"kinds": list(kinds or DEFAULT_KINDS)},
                                 "window": {"default": window}, "rows": []}, note="started blank")


def _mine(conn, key: str, actor: str) -> dict:
    row = _row(conn, key)
    if row["owner"] != actor:
        raise NotYours("only the person who made this report changes it; copy it to make your own")
    return row


def save(conn, key: str, d: dict, actor: str, base: int | None = None, note: str = "saved") -> dict:
    """Keep `d` as the report's next version, checked as a standard report is. `base` is the version the person
    edited from; when someone saved a newer one since, nothing is kept (Stale)."""
    row = _mine(conn, key, actor)
    if base is not None and base != row["version"]:
        raise Stale(f"this report was saved as version {row['version']} since you opened version {base}; "
                    "reload it to see that version")
    if not isinstance(d, dict):
        raise ReportError("a report definition is a mapping")
    version = row["version"] + 1
    stored = _plain(reports.check({**d, "key": key, "version": version}, "custom"))
    conn.execute("update custom_report set definition = %s, title = %s, version = %s, updated_by = %s, "
                 "updated_at = now() where id = %s", (Jsonb(stored), stored["title"], version, actor, row["id"]))
    _keep(conn, row["id"], version, stored, actor, note)
    conn.commit()
    return _row(conn, key)


def share(conn, key: str, shared: str, actor: str) -> dict:
    """Who else sees the report: nobody (''), everyone, or the holders of a workflow role ('role:<name>')."""
    row = _mine(conn, key, actor)
    shared = shared or ""
    if shared not in ("", EVERYONE):
        role = shared[len(ROLE):] if shared.startswith(ROLE) else ""
        if not role or not conn.execute("select 1 from work_role where name = %s and kind = 'person'",
                                        (role,)).fetchone():
            raise ReportError(f"{shared!r} isn't everyone or a workflow role people hold")
    conn.execute("update custom_report set shared = %s, updated_by = %s, updated_at = now() where id = %s",
                 (shared, actor, row["id"]))
    conn.commit()
    return _row(conn, key)


def versions(conn, key: str) -> list[dict]:
    """Every kept version, newest first, each with who saved it, when, and what it was."""
    row = _row(conn, key)
    rows = conn.execute("select version, definition, by_user, note, created_at from custom_report_version "
                        "where report_id = %s order by version desc", (row["id"],)).fetchall()
    return [{"version": r["version"], "title": r["definition"].get("title"), "by": r["by_user"], "note": r["note"],
             "at": r["created_at"], "panels": sum(len(x.get("panels") or []) for x in r["definition"].get("rows") or []),
             "current": r["version"] == row["version"]} for r in rows]


def restore(conn, key: str, version: int, actor: str) -> dict:
    """Bring back an earlier version as the newest one; every version in between stays kept."""
    row = _mine(conn, key, actor)
    old = conn.execute("select definition from custom_report_version where report_id = %s and version = %s",
                       (row["id"], version)).fetchone()
    if not old:
        raise ReportError(f"{row['title']} has no version {version}")
    return save(conn, key, old["definition"], actor, note=f"restored version {version}")


def remove(conn, key: str, actor: str) -> None:
    """Take the report down; it and its versions are kept, marked removed."""
    row = _mine(conn, key, actor)
    conn.execute("update custom_report set deleted_at = now(), updated_by = %s where id = %s", (actor, row["id"]))
    conn.commit()


def parse(text: str) -> dict:
    """A definition from the definition view's text: YAML, or JSON (which YAML reads)."""
    if len(text or "") > MAX_TEXT:
        raise ReportError("the definition is too long")
    try:
        d = yaml.safe_load(text or "")
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ReportError(f"the definition isn't YAML or JSON{where}: {getattr(e, 'problem', None) or e}") from None
    if not isinstance(d, dict):
        raise ReportError("a report definition is a mapping")
    return d


ORDER = ("schema", "title", "for", "scope", "window", "rows")


PANEL_ORDER = ("id", "type", "title", "size")


def _panel_order(p: dict) -> dict:
    out = {k: p[k] for k in PANEL_ORDER if k in p} | {k: v for k, v in p.items() if k not in PANEL_ORDER}
    if isinstance(out.get("panels"), list):  # a column's panels
        out["panels"] = [_panel_order(q) if isinstance(q, dict) else q for q in out["panels"]]
    return out


def dump(d: dict) -> str:
    """The definition as YAML, the fields in the order a person reads them (a panel's id, type, title and size first),
    without what the store keeps."""
    plain = _plain(d)
    plain["rows"] = [{**r, "panels": [_panel_order(p) for p in r.get("panels") or []]} for r in plain.get("rows") or []]
    ordered = {k: plain[k] for k in ORDER if k in plain} | {k: v for k, v in plain.items() if k not in ORDER}
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=120)
