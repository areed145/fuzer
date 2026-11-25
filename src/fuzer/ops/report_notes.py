"""Human notes on a report (docs/reports.md, the `note` panel): what people know that the numbers don't, written for one
report, scope and period, kept with author and time. A note's markdown may carry live values bound to measures, so the
number in the sentence is the number on the page:

    {production.oil}          the measure for the report's scope, window and day
    {work.backlog@area}       the same, for the area (or any kind) the scope sits in
    {work.backlog@fz-1a2b}    the same, for that entity

A note is pinned to the period it was written for (report, scope, window, at). A report shows its own period's notes
first, then the notes pinned to shorter periods that lie inside it (Tuesday's line shows on that week's page), each
with its period; an older period keeps its own.

    for_period(conn, pack, key, scope, window, at)    the notes to show, each with its live values
    add(conn, key, scope, window, at, body, by)       pin a new note
    edit(conn, key, nid, body, by) / remove(conn, key, nid, by)   the author's own note only
    values(conn, pack, body, scope, window, at)       the live values a body names, resolved now
"""

import re
from datetime import date

from ..core import metrics, windows
from . import report_pages

TOKEN = re.compile(r"\{([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)(?:@([A-Za-z0-9_.:-]+))?\}")
WINDOWS = windows.WINDOWS
MAX_BODY = 4000


class NoteError(ValueError):
    """A note that can't be kept as asked: empty, too long, or for an unknown window."""


class NotYours(PermissionError):
    """A note someone else wrote: only its author changes or removes it."""


def start(window: str, at: date) -> date:
    """The first day of the named window ending `at` (core/windows, the one place that says)."""
    try:
        return date.fromisoformat(windows.resolve(window, at)["start"])
    except windows.WindowError as e:
        raise NoteError(str(e)) from None


def _scope_for(conn, scope: str | None, at_: str | None) -> tuple[str | None, str | None]:
    """The entity a token's `@` names: an entity id as given, or the nearest one of that kind the scope sits in."""
    if not at_:
        return scope, None
    if at_.startswith("fz-"):
        return at_, None
    if not scope:
        return None, f"no scope to find the {at_} in"
    from ..graph import hierarchy

    row = conn.execute("select kind from entity where fuzer_id = %s", (scope,)).fetchone()
    if row and row["kind"] == at_:
        return scope, None
    hit = next((u["id"] for u in hierarchy.up(conn, scope) if u["kind"] == at_), None)
    return (hit, None) if hit else (None, f"the scope sits in no {at_}")


def values(conn, pack, body: str, scope: str | None, window: str, at: date | None) -> dict[str, dict]:
    """Each `{...}` in `body`, resolved for the scope, window and day (None: each measure's own latest day, as the
    report reads it when its link names no day): the measure's value, unit and label, or why not."""
    out: dict[str, dict] = {}
    for m in TOKEN.finditer(body or ""):
        token, key, where = m.group(0), m.group(1), m.group(2)
        if token in out:
            continue
        fid, why = _scope_for(conn, scope, where)
        if why:
            out[token] = {"key": key, "scope": None, "value": None, "error": why}
            continue
        m = metrics.registry().get(key)
        try:
            v = metrics.value(conn, pack, key, scope=fid, window=window, at=at if m and (m.dated or m.windowed) else None)
        except metrics.MetricError as e:
            out[token] = {"key": key, "scope": fid, "value": None, "error": str(e)}
            continue
        out[token] = {"key": key, "scope": fid, "value": v.get("value"), "unit": v.get("unit"), "label": v.get("label"),
                      "as_of": v.get("as_of"), "note": v.get("note"), "error": None}
    return out


def _row(r: dict) -> dict:
    return {"id": r["id"], "report": r["report_key"], "scope": r["scope"] or None, "window": r["span"],
            "at": r["at"].isoformat(), "start": start(r["span"], r["at"]).isoformat(), "body": r["body"],
            "by": r["by_user"], "written_at": r["created_at"].isoformat(timespec="seconds"),
            "edited_at": r["updated_at"].isoformat(timespec="seconds") if r["updated_at"] else None}


def for_period(conn, pack, key: str, scope: str | None, window: str, at: date, asked: bool = True) -> list[dict]:
    """The notes pinned to this period, then those pinned to shorter periods inside it, newest first, each with its
    live values (resolved for the note's own period, so a day's line keeps the day's number on a weekly page; a note
    ending on the day shown reads each measure's latest day when the report's link named no day, as its panels do)."""
    first = start(window, at)
    rows = conn.execute(
        "select * from report_note where report_key = %s and scope = %s and at between %s and %s and deleted_at is null "
        "order by at desc, created_at desc", (key, scope or "", first, at)).fetchall()
    own, inside = [], []
    for r in rows:
        if r["span"] not in WINDOWS:
            continue
        if r["span"] == window and r["at"] == at:
            own.append(r)
        elif start(r["span"], r["at"]) >= first:
            inside.append(r)
    mine = {r["id"] for r in own}
    return [_row(r) | {"own": r["id"] in mine, "values": values(conn, pack, r["body"], scope, r["span"],
                                                                    r["at"] if asked or r["at"] != at else None)}
            for r in own + inside]


def _clean(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise NoteError("a note needs some text")
    if len(body) > MAX_BODY:
        raise NoteError(f"a note is at most {MAX_BODY} characters")
    return body


def add(conn, key: str, scope: str | None, window: str, at: date, body: str, by: str) -> dict:
    start(window, at)  # refuses an unknown window
    r = conn.execute(
        "insert into report_note (report_key, scope, span, at, body, by_user) values (%s, %s, %s, %s, %s, %s) returning *",
        (key, scope or "", window, at, _clean(body), by)).fetchone()
    conn.commit()
    return _row(r)


def _mine(conn, key: str, nid: int, by: str) -> dict:
    r = conn.execute("select * from report_note where id = %s and report_key = %s and deleted_at is null",
                     (nid, key)).fetchone()
    if r is None:
        raise LookupError(f"no note {nid} on report {key}")
    if r["by_user"] != by:
        raise NotYours("only the person who wrote a note can change it")
    return r


def edit(conn, key: str, nid: int, body: str, by: str) -> dict:
    _mine(conn, key, nid, by)
    r = conn.execute("update report_note set body = %s, updated_at = now() where id = %s returning *",
                     (_clean(body), nid)).fetchone()
    conn.commit()
    return _row(r)


def remove(conn, key: str, nid: int, by: str) -> None:
    _mine(conn, key, nid, by)
    conn.execute("update report_note set deleted_at = now() where id = %s", (nid,))
    conn.commit()


@report_pages.panel_type("note")
def panel(conn, pack, report: dict, panel: dict, scope: dict, window: dict) -> dict:
    """The `note` panel's result: its fixed text with live values when the definition gives one (a section's heading
    line), else the people's notes for the period."""
    at, asked = date.fromisoformat(window["at"]), window.get("asked", True)
    if text := panel.get("text"):
        return {"text": text, "values": values(conn, pack, text, scope.get("id"), window["key"], at if asked else None),
                "notes": []}
    return {"text": None, "values": {},
            "notes": for_period(conn, pack, report["key"], scope.get("id"), window["key"], at, asked)}
