"""Incidents: what happened to one piece of plant around one time, as one thing you can open.

Signals are logged events (`involves` a tag), downtime records (`caused_by` a tag) and SAP
notifications (`concerns` a tag). Instrument tags roll up to the equipment tag they're part of,
so a vibration alarm on 20-VT-1001B and a trip of 20-P-1001B land on the same unit. Signals on a
unit are grouped while each is within WINDOW_DAYS of the previous one; a group becomes an
incident if it includes downtime or at least two kinds of signal (a lone notification is just
maintenance). One sorted pass per unit, after the graph is built.
"""

from collections import Counter, defaultdict
from datetime import date

from . import db
from .model import Candidate, Edge

WINDOW_DAYS = 3
SOURCE = "fuzer"
KINDS = {"event": ("involves", "timestamp"), "downtime": ("caused_by", "start"), "notification": ("concerns", "created")}
TITLE_PREFERENCE = ["downtime", "event", "notification"]


def _signals(conn) -> list[dict]:
    return conn.execute(
        """
        with parent as (
            -- instrument tag -> the equipment tag it belongs to
            select p.src as tag, p.dst as unit
            from edge p
            join entity a on a.fuzer_id = p.src and a.kind = 'tag'
            join entity b on b.fuzer_id = p.dst and b.kind = 'tag'
            where p.rel = 'part_of'
        )
        select s.fuzer_id as id, s.kind, coalesce(pa.unit, e.dst) as unit,
               left(coalesce(gt.value, gs.value, gc.value) #>> '{}', 10) as day,
               (gl.value #>> '{}')::numeric as lost_oil,
               coalesce(gd.value, gsum.value, gtx.value) #>> '{}' as text,
               gfm.value #>> '{}' as failure_mode,
               get.value #>> '{}' as event_type
        from edge e
        join entity s on s.fuzer_id = e.src and s.retired_at is null
        join entity t on t.fuzer_id = e.dst and t.kind = 'tag'
        left join parent pa on pa.tag = e.dst
        left join golden gt on gt.fuzer_id = s.fuzer_id and gt.field = 'timestamp' and s.kind = 'event'
        left join golden gs on gs.fuzer_id = s.fuzer_id and gs.field = 'start' and s.kind = 'downtime'
        left join golden gc on gc.fuzer_id = s.fuzer_id and gc.field = 'created' and s.kind = 'notification'
        left join golden gl on gl.fuzer_id = s.fuzer_id and gl.field = 'lost_oil_sm3'
        left join golden gd on gd.fuzer_id = s.fuzer_id and gd.field = 'comment'
        left join golden gsum on gsum.fuzer_id = s.fuzer_id and gsum.field = 'summary'
        left join golden gtx on gtx.fuzer_id = s.fuzer_id and gtx.field = 'short_text'
        left join golden gfm on gfm.fuzer_id = s.fuzer_id and gfm.field = 'failure_mode'
        left join golden get on get.fuzer_id = s.fuzer_id and get.field = 'event_type'
        where (e.rel = 'involves' and s.kind = 'event')
           or (e.rel = 'caused_by' and s.kind = 'downtime')
           or (e.rel = 'concerns' and s.kind = 'notification')
        """
    ).fetchall()


def derive(conn) -> tuple[list[Candidate], list[Edge]]:
    """Incident candidates (keyed `<unit>@<first day>`) and their edges."""
    by_unit: dict[str, list[dict]] = defaultdict(list)
    seen = set()
    for s in _signals(conn):
        if s["day"] and (s["id"], s["unit"]) not in seen:
            seen.add((s["id"], s["unit"]))
            by_unit[s["unit"]].append(s)
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (list(by_unit),))}

    cands, edges = [], []
    for unit, signals in by_unit.items():
        signals.sort(key=lambda s: (s["day"], s["kind"], s["id"]))
        group: list[dict] = []
        for s in signals + [None]:
            if s and group and (date.fromisoformat(s["day"]) - date.fromisoformat(group[-1]["day"])).days <= WINDOW_DAYS:
                group.append(s)
                continue
            if group and (any(g["kind"] == "downtime" for g in group) or len({g["kind"] for g in group}) >= 2):
                cands.append(_incident(unit, labels.get(unit) or unit, group))
            group = [s] if s else []
    for c in cands:
        edges.append(Edge(("ref", c.ref), "about", ("id", c.fields.pop("_unit")), SOURCE))
        for sid in c.fields.pop("_signals"):
            edges.append(Edge(("ref", c.ref), "includes", ("id", sid), SOURCE))
    return cands, edges


def _incident(unit: str, unit_label: str, group: list[dict]) -> Candidate:
    kinds = Counter(g["kind"] for g in group)
    titled = sorted(group, key=lambda g: (TITLE_PREFERENCE.index(g["kind"]), g["day"]))
    modes = sorted({g["failure_mode"] for g in group if g["failure_mode"]})
    types = sorted({g["event_type"] for g in group if g["event_type"]})
    key = f"{unit_label}@{group[0]['day']}"
    fields = {
        "description": f"{unit_label}: {titled[0]['text'] or 'incident'}",
        "unit": unit_label, "start": group[0]["day"], "end": group[-1]["day"], "timestamp": group[0]["day"],
        "signals": len(group), "events": kinds["event"], "downtime_records": kinds["downtime"],
        "notifications": kinds["notification"],
        "lost_oil_sm3": float(sum(g["lost_oil"] or 0 for g in group)),
        **({"failure_modes": modes} if modes else {}), **({"event_types": types} if types else {}),
        "_unit": unit, "_signals": [g["id"] for g in group],
    }
    return Candidate(SOURCE, "incident", key, "incident", key, fields)


def write_edges(conn, edges: list[Edge], assigned: dict[str, str]) -> int:
    rows = []
    for e in edges:
        src = assigned.get(e.src[1]) if e.src[0] == "ref" else e.src[1]
        dst = assigned.get(e.dst[1]) if e.dst[0] == "ref" else e.dst[1]
        if src and dst:
            rows.append((src, e.rel, dst, [e.source], 1.0))
    return db.copy_rows(conn, "edge", ["src", "rel", "dst", "sources", "confidence"], rows)
