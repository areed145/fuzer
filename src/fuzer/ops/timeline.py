"""Event lanes (P5.13a): what happened on a unit in a window, one lane per kind of record, to draw under its trend.

    lanes(conn, fuzer_id, start, end) -> {"unit", "start", "end", "lanes": [{"key", "label", "events": [...]}]}

The entity is read on its unit, as its trend is (`values.unit_of`). The lanes, in the order an IOC engineer reads an
upset:

- `alarms`: a point standing for the unit past an enabled alarm limit, from when it crossed to when it cleared
  (`around.alarms`, the historian at its 5-minute step).
- `log`: operator log entries that mention the unit or what stands for it (`around.log`).
- `cases`: the cases working an exception about the unit, from the earliest finding on them to the latest (the same
  reading of "about" and "when" as `around.window`), with their state.
- `maintenance`: notifications raised on the unit (a moment) and work orders on it (start to finish).
- `downtime`: downtime records the unit caused or was affected by, and incidents about it.

Every event carries the record it came from (`id`, and `item_id` for a case), so a marker opens it. Times are plant-local
ISO strings, like the trend's. Nothing here writes or reads a model.
"""

from datetime import datetime

from ..analysis import values
from . import around
from .exceptions import anchor_map, anchors

MAX_EVENTS = 200  # per lane, the earliest first: what reads on one chart

LANES = [("alarms", "Alarms"), ("log", "Operator log"), ("cases", "Cases"),
         ("maintenance", "Notifications and work orders"), ("downtime", "Downtime and incidents")]


def _targets(conn, unit: str) -> list[str]:
    """The unit, the equipment installed at it and the instruments on it: what its records name."""
    return [unit] + [r["id"] for r in conn.execute(
        """
        select src as id from edge where dst = %(u)s and rel = 'installed_at' and valid_to is null
        union select src from edge where dst = %(u)s and rel = 'part_of'
        order by 1
        """, {"u": unit})]


def _fields(conn, kind: str, rels: tuple[str, ...], targets: list[str], outward: bool = True) -> list[dict]:
    """Records of `kind` linked to a target by one of `rels` (the record as source, or with `outward` false, as
    destination), with their golden fields and label."""
    near, far = ("src", "dst") if outward else ("dst", "src")
    return conn.execute(
        f"""
        select en.fuzer_id as id, l.label, jsonb_object_agg(g.field, g.value #>> '{{}}') as f
        from entity en join golden g using (fuzer_id) left join entity_label l on l.fuzer_id = en.fuzer_id
        where en.kind = %(kind)s and en.retired_at is null and en.merged_into is null
          and en.fuzer_id in (select {near} from edge where rel = any(%(rels)s) and {far} = any(%(t)s))
        group by en.fuzer_id, l.label
        """, {"kind": kind, "rels": list(rels), "t": targets}).fetchall()


def _in(start: str | None, end: str | None, a: str, b: str) -> bool:
    """Whether [start, end] overlaps [a, b]; a day ('2025-07-17') counts as the whole day."""
    if not start:
        return False
    stop = end or start
    if len(stop) == 10:
        stop += "T23:59"
    return start <= b and stop >= a


def _maintenance(conn, targets: list[str], a: str, b: str) -> list[dict]:
    out = []
    for r in _fields(conn, "notification", ("concerns",), targets):
        f = r["f"]
        out.append({"id": r["id"], "kind": "notification", "label": r["label"], "start": f.get("created"),
                    "end": None, "text": f.get("short_text") or f.get("description"),
                    "detail": f.get("failure_mode_name") or f.get("notif_type")})
    for r in _fields(conn, "work_order", ("concerns",), targets):
        f = r["f"]
        out.append({"id": r["id"], "kind": "work_order", "label": r["label"], "start": f.get("start"),
                    "end": f.get("finish") or f.get("basic_finish"), "text": f.get("text") or f.get("description"),
                    "detail": f.get("order_type"), "status": f.get("status")})
    return [e for e in out if _in(e["start"], e["end"], a, b)]


def _downtime(conn, targets: list[str], a: str, b: str) -> list[dict]:
    out = []
    for r in _fields(conn, "downtime", ("affects", "caused_by"), targets):
        f = r["f"]
        out.append({"id": r["id"], "kind": "downtime", "label": r["label"], "start": f.get("start"),
                    "end": f.get("end"), "text": f.get("comment") or f.get("description"), "detail": f.get("reason")})
    for r in _fields(conn, "incident", ("about",), targets):
        f = r["f"]
        out.append({"id": r["id"], "kind": "incident", "label": r["label"], "start": f.get("start"),
                    "end": f.get("end"), "text": f.get("description"), "detail": None})
    return [e for e in out if _in(e["start"], e["end"], a, b)]


def _cases(conn, mine: set[str], a: str, b: str) -> list[dict]:
    """Cases working an exception about the unit, placed by their findings' times (else when first and last seen)."""
    rows = conn.execute(
        """
        select x.item_id, w.title, w.state, w.type, x.subjects, x.first_seen, x.last_seen,
               x.evidence->'finding'->>'start' as s, x.evidence->'finding'->>'end' as e
        from exception_record x join work_item w on w.id = x.item_id
        where x.item_id is not null
        """).fetchall()
    stands = anchor_map(conn, list({s for r in rows for s in r["subjects"] or []}))
    cases: dict[int, dict] = {}
    for r in rows:
        if not any(stands.get(s, set()) & mine for s in r["subjects"] or []):
            continue
        s = (r["s"] or r["first_seen"].isoformat())[:16]
        e = (r["e"] or r["s"] or r["last_seen"].isoformat())[:16]
        c = cases.setdefault(r["item_id"], {"id": None, "item_id": r["item_id"], "kind": "case", "label": r["title"],
                                            "start": s, "end": e, "text": r["title"], "detail": r["type"],
                                            "status": r["state"]})
        c["start"], c["end"] = min(c["start"], s), max(c["end"], e)
    return [c for c in cases.values() if _in(c["start"], c["end"], a, b)]


def lanes(conn, fuzer_id: str, start: str, end: str, src=None) -> dict:
    """The unit's records in [start, end], one lane per kind, each lane's events the earliest first."""
    row, unit = values.unit_of(conn, fuzer_id)
    if not row:
        return {"error": "unknown entity"}
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if b <= a:
        return {"error": "the window ends before it starts"}
    sa, sb = a.isoformat(timespec="minutes"), b.isoformat(timespec="minutes")
    targets = _targets(conn, unit)
    mine = anchors(conn, [unit])
    found = {
        "alarms": [{"id": x["point_id"], "kind": "alarm", "label": x["point"], "start": x["start"], "end": x["end"],
                    "text": f"{x['type']} {x['limit']}", "detail": x.get("level"), "peak": x["peak"]}
                   for x in around.alarms(conn, mine, a, b, src, limit=MAX_EVENTS)],
        "log": [{"id": x["id"], "kind": "log_entry", "label": x["author"], "start": x["at"], "end": None,
                 "text": x["text"], "detail": None} for x in around.log(conn, mine, a, b, limit=MAX_EVENTS)],
        "cases": _cases(conn, mine, sa, sb),
        "maintenance": _maintenance(conn, targets, sa, sb),
        "downtime": _downtime(conn, targets, sa, sb),
    }
    return {"unit": unit, "start": sa, "end": sb,
            "lanes": [{"key": k, "label": label,
                       "events": sorted(found[k], key=lambda e: (e["start"] or "", str(e["id"])))[:MAX_EVENTS]}
                      for k, label in LANES]}
