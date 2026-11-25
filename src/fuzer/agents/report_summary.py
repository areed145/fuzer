"""A report's AI note (docs/reports.md, the `summary` panel): the summary specialist reads one report's own panel
results for a scope, window and day, and says in a headline and a few points what they show. The note can only talk
about what is on the page, because the page is all it is given.

The entity summary's pattern (agents/summary.py), kept per period instead of per hours: one short call on
`agents.summary_model` at medium effort, kept per report, scope, window and day in `report_summary`, shared with everyone
who opens that report. Reading never calls a model. The daily report's checks apply (ops/daily_report.check): a point
with no citation, a citation that isn't in the facts, or a number that isn't in them is flagged on the note.

    facts(conn, pack, key, scope, window, at)        the report's panel results, made small enough for one call
    check(note, facts)                               {uncited_points, unknown_citations, unknown_numbers, ok}
    latest(conn, key, scope, window, at)             the kept note for that period, or None
    write(conn, pack, key, scope, window, at, by)    a new note from the facts now; callers check latest() first
"""

import json
import re
from datetime import date

from psycopg.types.json import Jsonb

from ..core import llm, models, reports, settings
from ..core.jsonsafe import plain
from ..ops import report_pages

SYSTEM = """You are the summary specialist of an oil and gas integrated operations centre. You are given the results
of every panel on one report page (its figures, trends and tables) for one scope (an entity in the asset register and
everything beneath it), one time window and the day the window ends.

Write what the people who read this report (named in `for`) need to take from the page first, from these facts only:
- headline: one sentence, at most 20 words, on how the scope did over the window.
- points: two to five short sentences, most important first: what is off target or moved most against the last
  window, where the losses or the work concentrate, what needs someone now.
A panel with `unknown: true` has no value because what it is read from isn't loaded: say it is unknown and why, in its
note's words (no bypass register loaded); never read it as zero, none or nothing to report.
Say numbers exactly as given, with their units; never add, subtract, round or work out a new number. Name entities by
their labels. Never advise how to operate the plant. If the panels show nothing of note, say so in the headline and give
one point at most. For each point list in `cites` what it rests on: measure keys (like "production.oil"), entity ids
("fz-..."), or case numbers as "case:<n>", taken from the facts."""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["headline", "points"],
    "properties": {
        "headline": {"type": "string"},
        "points": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "cites"],
            "properties": {"text": {"type": "string"}, "cites": {"type": "array", "items": {"type": "string"}}}}},
    },
}

COMMENTARY = ("summary", "note", "narrative", "view")  # text about the figures, or analyses with their own reads
LIST_CAP = 12   # rows of a table or cut kept for the call
SERIES_CAP = 4  # points of a long series kept: its first, its last, its lowest and its highest
FACTS_CAP = 24_000

NUMBER = re.compile(r"(?<![\w.:-])-?\d[\d,]*(?:\.\d+)?(?![\w:-])")
REF = re.compile(r"^(fz-[\w-]+|case:\d+)$")


def _series(xs: list) -> list:
    """A long series cut to its first, last, lowest and highest points, in order."""
    if len(xs) <= SERIES_CAP * 2 or not all(isinstance(x, dict) for x in xs):
        return xs
    vals = [(i, x.get("value")) for i, x in enumerate(xs) if isinstance(x.get("value"), (int, float))]
    keep = {0, len(xs) - 1}
    if vals:
        keep |= {min(vals, key=lambda p: p[1])[0], max(vals, key=lambda p: p[1])[0]}
    return [xs[i] for i in sorted(keep)]


def _small(x, depth: int = 0):
    """A panel result made small: long lists capped, series cut, plot drawing and styling dropped."""
    if isinstance(x, dict):
        return {k: _small(v, depth + 1) for k, v in x.items()
                if k not in ("layout", "config", "style", "color", "colors", "spec") and v not in (None, "", [], {})}
    if isinstance(x, list):
        if x and all(isinstance(i, dict) and "value" in i and "at" in i for i in x):
            return [_small(i, depth + 1) for i in _series(x)]
        return [_small(i, depth + 1) for i in x[:LIST_CAP]]
    if isinstance(x, float):
        return round(x, 2)
    return x


def _measures(panel: dict) -> list[str]:
    return [m for m in [panel.get("measure"), *(panel.get("measures") or []), panel.get("rows")] if m]


def facts(conn, pack, key: str, scope: str | None, window: str, at: date, asked: bool = True) -> dict | None:
    """The report's figure panels, each with its result, for one scope, window and day; None for an unknown report.
    Rows drawn once per entity beneath the scope (`repeat`) are left out: they are the drill-down, not the answer.
    Unless the day was `asked` for, the panels read each measure's own latest day, as the page does."""
    try:
        defn = report_pages.definition(conn, pack, key)
    except reports.ReportError:
        return None
    repeated = {r["id"] for r in defn["rows"] if r.get("repeat")}
    panels, scope_ref, resolved = [], None, None
    for p in reports.panels(defn):
        if p["type"] in COMMENTARY or p.get("row") in repeated:
            continue
        r = report_pages.panel_data(conn, pack, key, p["id"], scope, window, at if asked else None)
        scope_ref, resolved = scope_ref or r.get("scope"), resolved or r.get("window")
        if r.get("error"):
            continue
        result = {k: v for k, v in r.items() if k not in ("id", "type", "title", "scope", "window", "href", "error")
                  and (k != "unknown" or v)}  # said only where it is: the note gives the reason
        if p["type"] == "trend":  # the spec's series, without how to draw them
            spec = result.pop("spec", None) or {}
            result["series"] = [{"measure": s.get("point"), "label": s.get("label"), "unit": s.get("unit"),
                                 "values": [{"at": d, "value": v} for d, v in s.get("values") or []],
                                 "min": s.get("min"), "max": s.get("max"), "last": s.get("last")}
                                for s in spec.get("series") or []]
        if p["type"] == "figure":
            result.pop("figure", None)  # the rows carry the same numbers
        panels.append({"id": p["id"], "type": p["type"], "title": r.get("title") or p.get("title"),
                       "measures": _measures(p), "result": _small(result)})
    out = {"report": key, "title": defn.get("title"), "for": defn.get("for") or [], "scope": scope_ref,
           "window": (resolved or {}).get("label") or window, "at": at.isoformat(), "panels": panels}
    while len(json.dumps(out, default=str)) > FACTS_CAP and any(len(json.dumps(p["result"], default=str)) > 400
                                                                for p in panels):
        big = max(panels, key=lambda p: len(json.dumps(p["result"], default=str)))
        big["result"] = {"cut": "too large for the note", "value": (big["result"] or {}).get("value")}
    return plain(out)


def _walk(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v)
    else:
        yield x


def _val(n) -> float:
    return round(abs(float(str(n).replace(",", ""))), 1)


def allowed(f: dict) -> tuple[set[str], set[float]]:
    """What a point may cite (refs, measure keys and panel ids in the facts) and the numbers it may say."""
    cites, numbers = set(), set()
    for p in f.get("panels") or []:
        cites.add(p["id"])
        cites |= set(p.get("measures") or [])
    if isinstance(f.get("scope"), dict) and f["scope"].get("id"):
        cites.add(f["scope"]["id"])
    for v in _walk(f.get("panels")):
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            numbers.add(_val(v))
        elif isinstance(v, str):
            if REF.match(v):
                cites.add(v)
            elif re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+", v):
                cites.add(v)  # a measure or row set key named in a result
            else:
                numbers |= {_val(n) for n in NUMBER.findall(v)}
    return cites, numbers


def check(note: dict, f: dict) -> dict:
    """The daily report's checks on a note: points with no citation, citations not in the facts, numbers not in them."""
    cites, numbers = allowed(f)
    numbers |= {float(i) for i in range(6)}  # "two of the five" needs no source
    numbers |= {_val(n) for n in NUMBER.findall(f"{f.get('window') or ''} {f.get('at') or ''}".replace("-", " "))}
    uncited, unknown_cites, unknown_numbers = [], set(), set()
    for p in note.get("points") or []:
        if not p.get("cites"):
            uncited.append(p["text"][:120])
        unknown_cites |= {c for c in p.get("cites") or [] if c not in cites}
    for text in [note.get("headline") or ""] + [p["text"] for p in note.get("points") or []]:
        unknown_numbers |= {n for n in NUMBER.findall(text) if _val(n) not in numbers}
    return {"uncited_points": uncited, "unknown_citations": sorted(unknown_cites),
            "unknown_numbers": sorted(unknown_numbers),
            "ok": not (uncited or unknown_cites or unknown_numbers)}


def _row(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {"report": r["report_key"], "scope": r["scope"] or None, "window": r["span"], "at": r["at"].isoformat(),
            "headline": r["headline"], "points": r["points"], "checks": r["checks"],
            "written_at": r["created_at"].isoformat(timespec="seconds"), "by": r["by_user"],
            "model": (r["usage"] or {}).get("model"), "cost_usd": (r["usage"] or {}).get("cost_usd")}


def latest(conn, key: str, scope: str | None, window: str, at: date) -> dict | None:
    return _row(conn.execute(
        "select * from report_summary where report_key = %s and scope = %s and span = %s and at = %s "
        "order by created_at desc limit 1", (key, scope or "", window, at)).fetchone())


def write(conn, pack, key: str, scope: str | None, window: str, at: date, by: str, client=None,
          asked: bool = True) -> dict | None:
    """Write and keep a new note for the period; None for an unknown report."""
    f = facts(conn, pack, key, scope, window, at, asked)
    if f is None:
        return None
    model = settings.get(conn, "agents.summary_model") or settings.get(conn, "agents.model")
    message = llm.call(client or llm.client("agents"), {
        "model": model, "max_tokens": 1200,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": json.dumps(f, ensure_ascii=False, default=str)}],
        "output_config": {"effort": models.DEFAULT_EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}}},
        scope="agents")
    if message.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"The note stopped early ({message.stop_reason}); nothing was kept.")
    out = json.loads(next(b.text for b in message.content if b.type == "text"))
    measured = llm.usage(message)
    usage = {"model": model, **measured, **llm.cost_report(model, measured, "agents")}
    note = {"headline": (out.get("headline") or "").strip(),
            "points": [{"text": p["text"].strip(), "cites": [str(c) for c in p.get("cites") or []][:6]}
                       for p in (out.get("points") or [])[:5] if (p.get("text") or "").strip()]}
    conn.execute(
        "insert into report_summary (report_key, scope, span, at, headline, points, facts, checks, usage, by_user) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (key, scope or "", window, at, note["headline"], Jsonb(note["points"]), Jsonb(f), Jsonb(check(note, f)),
         Jsonb(usage), by))
    conn.commit()
    return latest(conn, key, scope, window, at)


@report_pages.panel_type("summary")
def panel(conn, pack, report: dict, panel: dict, scope: dict, window: dict) -> dict:
    """The `summary` panel's result: the kept note for the period, whether one can be written here, and whether this
    visit should write it unasked (none kept, the report's default scope and window, ending on its default day). Never calls a
    model."""
    at = date.fromisoformat(window["at"])
    kept = latest(conn, report["key"], scope.get("id"), window["key"], at)
    today = report_pages.default_at(conn, report) == window["at"]  # today, or the last production day
    return {"summary": kept, "can_write": llm.has_credentials("agents"),
            "due": kept is None and today and window["key"] == report["window"]["default"]
            and scope.get("id") == report_pages.default_scope(conn, pack, report)["id"],
            "as_of": kept["at"] if kept else None, "refs": sorted({c for p in (kept or {}).get("points") or []
                                                                   for c in p["cites"] if REF.match(c)})}
