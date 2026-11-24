"""The YAML measures and row sets read (core/measures.py, core/rowsets.py): the register's records of a kind, placed in
a scope by what they link to, dated by a field and counted or added up over a window. Registered in the metric
registry and the row set registry when this module is imported, with the register's roll-up (graph.hierarchy) as the
scope every scoped metric and row set uses.

    records(conn, src, q)          a measure's `from:` records, read once per call into the query's cache
    ancestors(conn, ids, kind, c)  each id's nearest entity of a kind at or above it, for a kind grain
    compute(m, conn, pack, q)      a YAML measure's value, refs and rows
    rows(spec, conn, pack, q)      a YAML row set's rows
"""

from statistics import mean

from ..core import measures, metrics, rowsets, windows
from ..core.measures import REFS
from ..core.metrics import Metric
from ..graph import hierarchy


@metrics.rollup
def beneath(conn, fid: str) -> set[str]:
    """Everything beneath an entity in the register: the entity pages' roll-up."""
    return set(hierarchy.subtree(conn, fid))


# ---- reading records --------------------------------------------------------------------------------------------------

def _number(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _text(v) -> str | None:
    return None if v is None else str(v)


def _folded(v) -> str | None:
    return None if v is None else str(v).strip().lower()


def _beyond(conn, links: list[list[str]], first: set[str]) -> dict[str, list[str]]:
    """For a link written `a.b` (a bypass `bypasses` a safeguard that `protects` a compressor), what the first hop's
    ends link to by the second: {first end: [second ends]}."""
    out: dict[str, list[str]] = {}
    for rel in sorted({p[1] for p in links if len(p) > 1}):
        for e in conn.execute("select src, dst from edge where src = any(%s) and rel = %s and "
                              "(valid_to is null or valid_to >= current_date)", (sorted(first), rel)):
            out.setdefault(e["src"], []).append(e["dst"])
    return out


def records(conn, src: dict, q, extra=()) -> list[dict]:
    """Every live record of the kind, with the fields the measure reads, what it links to and its times parsed; read
    once per `value` call (kept in the query's cache)."""
    fields = sorted({src["at"], *(f for f in (src.get("end"), src.get("value"), src.get("over")) if f),
                     *(src.get("where") or {}), *(src.get("unless") or {}), *extra})
    key = ("records", src["kind"], tuple(fields), tuple(src.get("links") or []),
           tuple(sorted((k, tuple(v)) for k, v in (src.get("where") or {}).items())),
           tuple(sorted((k, tuple(v)) for k, v in (src.get("unless") or {}).items())))
    if key in q.cache:
        return q.cache[key]
    links = [str(x).split(".") for x in src.get("links") or []]
    rows = conn.execute(
        """
        select p.fuzer_id as id, coalesce(l.label, p.fuzer_id) as label,
               coalesce(jsonb_object_agg(g.field, g.value) filter (where g.field is not null), '{}') as f,
               (select coalesce(array_agg(distinct e.dst), '{}') from edge e
                 where e.src = p.fuzer_id and e.rel = any(%(links)s)) as subjects
        from entity p
        left join golden g on g.fuzer_id = p.fuzer_id and g.field = any(%(fields)s)
        left join entity_label l on l.fuzer_id = p.fuzer_id
        where p.kind = %(kind)s and p.retired_at is null and p.merged_into is null
        group by p.fuzer_id, l.label
        """, {"kind": src["kind"], "fields": fields, "links": sorted({p[0] for p in links})}).fetchall()
    beyond = _beyond(conn, links, {s for r in rows for s in r["subjects"]})
    out = []
    for r in rows:
        f = r["f"] or {}
        if any(_folded(f.get(k)) not in {_folded(x) for x in v} for k, v in (src.get("where") or {}).items()):
            continue
        if any(_folded(f.get(k)) in {_folded(x) for x in v} for k, v in (src.get("unless") or {}).items()):
            continue
        near = [r["id"], *r["subjects"]]
        near += [t for s in r["subjects"] for t in beyond.get(s, ())]
        out.append({"id": r["id"], "label": r["label"], "f": f, "subjects": near,
                    "at": windows.parse_time(f.get(src["at"])),
                    "end": windows.parse_time(f.get(src["end"])) if src.get("end") else None})
    q.cache[key] = out
    return out


def ancestors(conn, ids: list[str], kind: str, cache: dict) -> dict[str, dict]:
    """Each id's nearest entity of `kind` at or above it in the register: {id: {id, label}}."""
    key = ("ancestors", kind)
    known = cache.setdefault(key, {})
    want = sorted(set(ids) - set(known))
    if want:
        for i in want:
            known[i] = None
        for r in conn.execute(
            """
            with recursive up (start, id, depth) as (
                select s, s, 0 from unnest(%(ids)s::text[]) s
                union
                select up.start, e.dst, up.depth + 1 from up
                join edge e on e.src = up.id and e.rel = any(%(rels)s) and (e.valid_to is null or e.valid_to >= current_date)
                where up.depth < %(max)s)
            select distinct on (up.start) up.start, up.id, coalesce(l.label, up.id) as label
            from up join entity n on n.fuzer_id = up.id and n.merged_into is null and n.kind = %(kind)s
            left join entity_label l on l.fuzer_id = up.id
            order by up.start, up.depth, up.id
            """, {"ids": want, "rels": list(hierarchy.CONTAINMENT), "max": hierarchy.MAX_DEPTH, "kind": kind}):
            known[r["start"]] = {"id": r["id"], "label": r["label"]}
    return {i: known[i] for i in ids if known.get(i)}


def _aggregate(m: dict, rows: list[dict], q) -> float | None:
    src, agg = m["from"], m.get("aggregate", "count")
    if agg == "count":
        return len(rows)
    vals = [v for v in (_number(r["f"].get(src["value"])) for r in rows) if v is not None] if src.get("value") else []
    if agg == "sum":
        return round(sum(vals), 2)
    if agg == "mean":
        return round(mean(vals), 2) if vals else None
    if agg == "ratio":
        over = sum(v for v in (_number(r["f"].get(src["over"])) for r in rows) if v is not None)
        return round(100 * sum(vals) / over, 1) if over else None
    ages = [(q.span.end - r["at"]).total_seconds() / 86400 for r in rows if r["at"]]
    return round(mean(ages), 1) if ages else None


def compute(m: dict, conn, pack, q) -> dict:
    src = m["from"]
    cuts = m.get("cuts") or {}
    every = [r for r in records(conn, src, q, cuts.values()) if q.within(r["subjects"])]
    if not every:
        what = src["kind"].replace("_", " ")
        return {"note": f"no {what} records {'within this scope' if q.scope else 'in the register'}"}
    if m.get("stock"):
        t = q.span.end
        rows = [r for r in every if r["at"] and r["at"] <= t and (r["end"] is None or r["end"] > t)]
    else:
        rows = [r for r in every if q.span.holds(r["at"])]
    out = {"value": _aggregate(m, rows, q), "refs": [r["id"] for r in rows][:REFS], "as_of": q.span.at.isoformat()}
    if q.grain:
        groups: dict[str, tuple[str, list]] = {}
        if q.grain in cuts:
            for r in rows:
                k = _text(r["f"].get(cuts[q.grain])) or "Not given"
                groups.setdefault(k, (k.replace("_", " ").capitalize(), []))[1].append(r)
        else:
            subjects = sorted({s for r in rows for s in r["subjects"]})
            up = ancestors(conn, subjects, q.grain, q.cache)
            for r in rows:
                hit = {up[s]["id"]: up[s]["label"] for s in r["subjects"] if s in up and q.within([up[s]["id"]])}
                for k, label in hit.items():
                    groups.setdefault(k, (label, []))[1].append(r)
        out["rows"] = sorted(({"key": k, "label": label, "value": _aggregate(m, rs, q), "refs": [r["id"] for r in rs][:REFS]}
                              for k, (label, rs) in groups.items()), key=lambda x: (-(x["value"] or 0), x["label"]))
    return out


def _metric(key: str, m: dict, source: str) -> Metric:
    return Metric(key, m["label"], m["definition"], m["formula"], m["unit"], m["records"],
                  lambda conn, pack, q, _m=m: compute(_m, conn, pack, q), tuple(m.get("grains") or []),
                  m.get("standard"), m.get("period") or ("open at the window's end" if m.get("stock") else
                                                         f"the window ({m.get('window', '30d')} by default)"),
                  m.get("better", "higher"), m.get("group", "Other"), False, m.get("target"), True, True,
                  bool(m.get("stock")), m.get("cutoff", "day"), m.get("window", "30d"), source)


@metrics.provider
def declared(pack=None) -> dict:
    """{key: Metric}: the platform's YAML measures, with the pack's on top (its own, or rebinding one of ours)."""
    out = {k: _metric(k, m, "platform") for k, m in measures.builtin().items()}
    out |= {k: _metric(k, m, "bundle") for k, m in (getattr(pack, "measures", None) or {}).items()}
    return out


# ---- row sets ---------------------------------------------------------------------------------------------------------

def _cell(r: dict, f: str, labels: dict):
    if f == "@label":
        return r["label"]
    if f == "@at":
        return r["at"].isoformat(timespec="minutes") if r["at"] else None
    if f == "@subjects":
        return ", ".join(sorted({labels.get(s, s) for s in r["subjects"][1:]})) or None
    v = r["f"].get(f)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    return None if v is None else str(v)


@rowsets.engine
def rows(spec: dict, conn, pack, q) -> dict:
    """A YAML row set's rows: its records in scope, dated in the window (or open at its end), in its order."""
    what, _, way = (spec.get("order") or "@at desc").partition(" ")
    shown = [c["field"] for c in spec["columns"] if not c["field"].startswith("@")]
    every = [r for r in records(conn, spec["from"], q, [*shown, *([] if what.startswith("@") else [what])])
             if q.within(r["subjects"])]
    if spec.get("stock"):
        t = q.span.end
        rows = [r for r in every if r["at"] and r["at"] <= t and (r["end"] is None or r["end"] > t)]
    else:
        rows = [r for r in every if q.span.holds(r["at"])]

    def key(r):
        return r["at"] if what == "@at" else _number(r["f"].get(what))
    rows = sorted((r for r in rows if key(r) is not None), key=key, reverse=way != "asc") + \
        [r for r in rows if key(r) is None]
    want = sorted({s for r in rows for s in r["subjects"][1:]})
    labels = {x["fuzer_id"]: x["label"] for x in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (want,))} if want else {}
    note = None if every else f"no {spec['from']['kind'].replace('_', ' ')} records " + \
        ("within this scope" if q.scope else "in the register")
    return {"rows": [{"key": r["id"], "cells": {c["key"]: _cell(r, c["field"], labels) for c in spec["columns"]},
                      "href": f"/e/{r['id']}"} for r in rows], "note": note}
