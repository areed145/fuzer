"""Operating limits per PI point (#101): alarm limits, integrity operating windows (IOWs, API RP 584),
normal ranges learned from history, the operating mode of each unit, and excursions outside them.

Limits arrive as data through mappings with a `limits` block: PI AF limit traits (Hi / HiHi / Lo /
LoLo on an attribute), DCS alarm exports and IOW registers (class, low and high limit, time allowed
outside). The rebuild lands them in point_limit against the timeseries entity, dated and with their
source record; an IOW written against an instrument tag counts for that tag's one point.

Which limit counts: for alarm limits, the first source in the pack's `limits.priority` that gives one
(enabled, valid at the time); IOWs all count; a point with neither gets the normal range learned from
its own history while its unit runs (`learn`, labelled learned). Checks compare the sources with each
other and with the learned ranges (`issues`).

A unit (the equipment tag a point's instrument belongs to, or the well) runs when its run signal, the
first of its points matching the pack's `limits.run_signals`, is on; units without one follow the units
around them. Excursions are counted only while the unit runs, so a stopped pump's low flow isn't one.
`scan` records them, and an IOW breach past its allowed time (critical or standard) opens an `iow_breach`
work item for an engineer, one per IOW while it is open.
"""

import itertools
import re
from collections import Counter
from datetime import datetime, timedelta

import numpy as np

from ..core import db, detectors
from ..core.exceptions import ExceptionError, exception_type, raise_exception
from ..core.findings import DETERMINISTIC, Finding, asset, fact
from ..core.findings import at as moment

COLS = ["record", "limit_type", "point_id", "source", "basis", "value", "unit", "reported", "level", "allowed_minutes",
        "enabled", "valid_from", "valid_to", "note"]
TYPES = ("lolo", "lo", "hi", "hihi")
HIGH = {"hi", "hihi"}
DEFAULTS = {"priority": ["dcs", "pi_af"], "run_signals": [{"uom": "kW", "above": 20}, {"uom": "rpm", "above": 1000}]}
IOW_BREACH = exception_type("iow_breach", "IOW breach", process="iow_breach", audience=("ioc", "field"),
                            description="An integrity operating window held past its allowed time.").name
WORK_LEVELS = {"critical": 2.0, "standard": 1.0}  # IOW classes whose breaches past their time open work, by priority
MERGE_MINUTES = 30  # outside again within this long is the same excursion
DEADBAND = 0.01  # of the limit: how far back inside a value must come to end an excursion
SUSPECT = {"standing", "implausible", "order"}  # checks that mean a limit isn't used for excursions


def config(pack=None) -> dict:
    return {**DEFAULTS, **(getattr(pack, "limits", None) or {})}


# ---- landing --------------------------------------------------------------------------------------

def build(conn, pack, maps: dict, endpoint, extra=()) -> dict:
    """Every limits dataset into point_limit (`endpoint` resolves points and tags, as graph.resolver). `extra`:
    (source, Limit) pairs read elsewhere, such as the P&IDs' alarm and trip tables (basis `design`)."""
    found, unresolved = [], Counter()
    for source, x in extra:
        fid = next((f for f, _ in map(endpoint, x.of) if f), None)
        if fid is None:
            unresolved[f"{source}: {endpoint(x.of[-1])[1]}"] += 1
        else:
            found.append((source, x, fid))
    for m in maps.values():
        if not m.limit_spec:
            continue
        for rec in conn.execute("select source_key, payload from source_record where source = %s and record_type = %s",
                                (m.source, m.record_type)):
            for x in m.limits(rec["source_key"], rec["payload"], pack):
                fid = next((f for f, _ in map(endpoint, x.of) if f), None)
                if fid is None:
                    unresolved[f"{m.source} {m.record_type}: {endpoint(x.of[-1])[1]}"] += 1
                    continue
                found.append((m.source, x, fid))
    # A tag stands for its point when it has exactly one (an instrument and its PV).
    ids = list({f for _, _, f in found})
    kinds = {r["fuzer_id"]: r["kind"] for r in conn.execute(
        "select fuzer_id, kind from entity where fuzer_id = any(%s)", (ids,))}
    points = {r["src"]: r["pts"] for r in conn.execute(
        """
        select e.src, array_agg(distinct e.dst) as pts from edge e join entity_label l on l.fuzer_id = e.dst
        where e.rel = 'has_timeseries' and e.src = any(%s) and l.label not like '%%.SP' and l.label not like '%%.OP'
        group by e.src
        """, ([f for f in ids if kinds.get(f) != "timeseries"],))}
    rows = {}
    for source, x, fid in found:
        if kinds.get(fid) != "timeseries":
            pts = points.get(fid, [])
            if len(pts) != 1:
                unresolved[f"{source} {x.record.split(':')[1]}: {kinds.get(fid)} with {len(pts)} points"] += 1
                continue
            fid = pts[0]
        rows[(x.record, x.limit_type)] = (x.record, x.limit_type, fid, source, x.basis, x.value, x.unit, x.reported,
                                          x.level, x.allowed_minutes, x.enabled, x.valid_from, x.valid_to, x.note)
    sync = db.sync_table(conn, "point_limit", COLS[:2], COLS[2:], rows.values())
    return {"limits": len(rows), "unresolved_limits": dict(unresolved),
            "limit_changes": {k: v for k, v in sync.items() if k != "rows"}}


# ---- which limits count ---------------------------------------------------------------------------

def _valid(r: dict, at: datetime | None) -> bool:
    if at is None:
        return r["valid_to"] is None
    return (r["valid_from"] is None or r["valid_from"] <= at) and (r["valid_to"] is None or at < r["valid_to"])


def _iow_id(record: str) -> str:
    """The IOW's own id: the first part of its record key (IOW-001 of iow:window:IOW-001>B)."""
    return record.split(":", 2)[2].split(">")[0]


def effective(rows: list[dict], normals: dict | None, priority: list[str], at: datetime | None = None) -> dict:
    """What counts for one point at `at` (now when None): alarm limits by type (the first source in
    `priority`, enabled and valid), IOWs (both sides of each), and the learned range when no source gives
    any. `rows` are its point_limit rows; `normals` its point_normal rows by mode."""
    rank = {s: i for i, s in enumerate(priority)}
    alarm: dict[str, dict] = {}
    for r in sorted((r for r in rows if r["basis"] == "alarm" and r["enabled"] and _valid(r, at)),
                    key=lambda r: (rank.get(r["source"], len(rank)), r["source"])):
        alarm.setdefault(r["limit_type"], {"value": r["value"], "source": r["source"], "record": r["record"],
                                           "level": r["level"]})
    iows: dict[str, dict] = {}
    for r in rows:
        if r["basis"] == "iow" and r["enabled"] and _valid(r, at):
            w = iows.setdefault(r["record"], {"record": r["record"], "iow": _iow_id(r["record"]), "level": r["level"],
                                              "allowed_minutes": r["allowed_minutes"], "unit": r["unit"],
                                              "note": r["note"], "lo": None, "hi": None})
            w["lo" if r["limit_type"] in ("lo", "lolo") else "hi"] = r["value"]
    out = {"alarm": alarm, "iow": sorted(iows.values(), key=lambda w: w["iow"])}
    run = (normals or {}).get("running") or (normals or {}).get("any")
    if run:
        lo, hi = learned_band(run)
        out["normal"] = {"p01": run["p01"], "p50": run["p50"], "p99": run["p99"], "mode": run["mode"]}
        if not alarm and not iows:
            out["learned"] = {"lo": lo, "hi": hi, "mode": run["mode"], "since": _iso(run["since"]),
                              "until": _iso(run["until"])}
    return out


def lines(e: dict) -> list[dict]:
    """One point's effective limits as flat lines to draw on a trend or quote: {type, value, basis, and
    source / level / iow}."""
    out = [{"type": t, "value": x["value"], "basis": "alarm", "source": x["source"]}
           for t, x in sorted(e["alarm"].items(), key=lambda kv: TYPES.index(kv[0]))]
    for w in e["iow"]:
        out += [{"type": side, "value": w[side], "basis": "iow", "level": w["level"], "iow": w["iow"],
                 "allowed_minutes": w["allowed_minutes"]} for side in ("lo", "hi") if w[side] is not None]
    if lr := e.get("learned"):
        out += [{"type": side, "value": lr[side], "basis": "learned", "mode": lr["mode"]} for side in ("lo", "hi")]
    return out


def learned_band(n: dict) -> tuple[float, float]:
    """The range a point stays in while its unit runs, with a margin: what counts as outside when no
    source gives a limit."""
    margin = max(0.5 * (n["p99"] - n["p01"]), 0.05 * abs(n["p50"]), 1e-3)
    return round(n["p01"] - margin, 3), round(n["p99"] + margin, 3)


def _iso(t):
    return t.isoformat() if hasattr(t, "isoformat") else t


def rows_for(conn, point_ids: list[str]) -> tuple[dict[str, list], dict[str, dict]]:
    """point_limit rows and point_normal rows (by mode) per point."""
    limits: dict[str, list] = {}
    for r in conn.execute("select * from point_limit where point_id = any(%s) order by record, limit_type", (point_ids,)):
        limits.setdefault(r["point_id"], []).append(r)
    normals: dict[str, dict] = {}
    for r in conn.execute("select * from point_normal where point_id = any(%s)", (point_ids,)):
        normals.setdefault(r["point_id"], {})[r["mode"]] = r
    return limits, normals


def for_points(conn, pack, point_ids: list[str], at: datetime | None = None) -> dict[str, dict]:
    """Effective limits per point (see effective); `pack` may be None for the default priority."""
    limits, normals = rows_for(conn, point_ids)
    pri = config(pack)["priority"]
    return {p: effective(limits.get(p, []), normals.get(p), pri, at) for p in point_ids}


def for_entity(conn, pack, fuzer_id: str, at: str | None = None, excursions: int = 10) -> dict:
    """The limits on a point, or on every point of a unit (its own and its instruments'), with each
    source's values, the checks that fail and the latest excursions."""
    from ..sources.timeseries import _point, _unit_points

    kind = conn.execute("select kind from entity where fuzer_id = %s", (fuzer_id,)).fetchone()
    if not kind:
        return {"error": "unknown entity"}
    pts = [p for p in [_point(conn, fuzer_id)] if p] if kind["kind"] == "timeseries" else _unit_points(conn, fuzer_id, 200)
    ids = [p["id"] for p in pts]
    when = datetime.fromisoformat(at) if at else None
    limits, normals = rows_for(conn, ids)
    pri = config(pack)["priority"]
    found = {}
    for i in issues(conn, pack, ids):
        found.setdefault(i["point_id"], []).append({k: v for k, v in i.items() if k not in ("point_id", "point")})
    exc: dict[str, list] = {}
    for r in conn.execute(
            "select * from (select *, row_number() over (partition by point_id order by start desc) as n from excursion "
            "where point_id = any(%s)) x where n <= %s order by start desc", (ids, excursions)):
        exc.setdefault(r["point_id"], []).append(_excursion_out(r))
    out = []
    for p in pts:
        e = effective(limits.get(p["id"], []), normals.get(p["id"]), pri, when)
        out.append({"id": p["id"], "point": p["point"], "description": p["description"], "uom": p["uom"], **e,
                    "sources": [{"source": r["source"], "type": r["limit_type"], "value": r["value"],
                                 "reported": r["reported"], "basis": r["basis"], "level": r["level"],
                                 "enabled": r["enabled"], "valid_from": _iso(r["valid_from"]),
                                 "valid_to": _iso(r["valid_to"]), "record": r["record"]} for r in limits.get(p["id"], [])],
                    "issues": found.get(p["id"], []), "excursions": exc.get(p["id"], [])})
    return {"id": fuzer_id, "at": at, "points": out,
            "without_limits": [p["point"] for p in out if not p["alarm"] and not p["iow"]]}


# ---- checks ---------------------------------------------------------------------------------------

def issues(conn, pack, point_ids: list[str] | None = None) -> list[dict]:
    """Limits that look wrong or are missing, from the current rows:
    - disagree: two sources give the same limit different values (AF not updated after an MOC)
    - order: a source's limits out of order (Hi above HiHi, Lo above Hi)
    - disabled: an alarm switched off in its source
    - standing: an alarm limit inside the learned normal range (in alarm most of the time it runs)
    - implausible: a high limit many times the normal value (often typed in another unit)
    - missing: no alarm limits, where most points measuring the same thing have them
    - drawing: the alarm and trip table on a P&ID (basis design) differs from the alarm that is configured, or
      shows a limit no alarm source has (#475)."""
    where = "where l.point_id = any(%(ids)s)" if point_ids is not None else ""
    rows = conn.execute(f"select l.*, e.label from point_limit l left join entity_label e on e.fuzer_id = l.point_id "
                        f"{where} order by l.point_id, l.record", {"ids": point_ids}).fetchall()
    normal = {r["point_id"]: r for r in conn.execute(
        f"select * from point_normal n where mode = 'running' {'and point_id = any(%(ids)s)' if point_ids is not None else ''}",
        {"ids": point_ids})}
    by_point: dict[str, list] = {}
    for r in rows:
        if r["valid_to"] is None:
            by_point.setdefault(r["point_id"], []).append(r)
    out = []

    def add(r, fault, detail, **kw):
        out.append({"point_id": r["point_id"], "point": r["label"], "fault": fault, "detail": detail, **kw})

    for pid, rs in by_point.items():
        alarms = [r for r in rs if r["basis"] == "alarm"]
        for r in alarms:
            if not r["enabled"]:
                add(r, "disabled", f"{r['source']} {r['limit_type']} alarm is disabled", source=r["source"],
                    type=r["limit_type"])
        by_type: dict[str, list] = {}
        for r in alarms:
            by_type.setdefault(r["limit_type"], []).append(r)
        for t, same in by_type.items():
            vals = {r["source"]: r["value"] for r in same}
            if len(vals) > 1 and max(vals.values()) - min(vals.values()) > max(0.02 * max(abs(v) for v in vals.values()), 1e-6):
                add(same[0], "disagree", ", ".join(f"{s} {v:g}" for s, v in sorted(vals.items())), type=t)
        out += _drawing(rs, by_type, config(pack)["priority"])
        by_source: dict[str, dict] = {}
        for r in alarms:
            by_source.setdefault(r["source"], {})[r["limit_type"]] = r
        for src, lim in by_source.items():
            ordered = [(t, lim[t]["value"]) for t in TYPES if t in lim]
            for (ta, a), (tb, b) in itertools.pairwise(ordered):
                if a >= b:
                    add(lim[ta], "order", f"{src} {ta} {a:g} is not below {tb} {b:g}", source=src, type=ta)
        n = normal.get(pid)
        if not n:
            continue
        band = max(n["p99"] - n["p01"], 0.02 * abs(n["p50"]), 1e-6)
        for r in alarms:
            v, high = r["value"], r["limit_type"] in HIGH
            if (high and v < n["p50"]) or (not high and v > n["p50"]):
                add(r, "standing", f"{r['source']} {r['limit_type']} {v:g} is inside the normal range "
                    f"({n['p01']:.3g} to {n['p99']:.3g}): in alarm most of the time it runs", source=r["source"],
                    type=r["limit_type"])
            elif high and abs(v - n["p50"]) > 10 * band and abs(v) > 8 * abs(n["p50"]):
                add(r, "implausible", f"{r['source']} {r['limit_type']} {v:g} is {abs(v / n['p50']) if n['p50'] else 0:.0f}x the "
                    f"normal value {n['p50']:.3g}: another unit?", source=r["source"], type=r["limit_type"])
    out += _missing(conn, point_ids)
    return out


def drawing_issues(conn, pack=None) -> list[dict]:
    """Every `drawing` issue: each point a P&ID's alarm and trip table rates whose configured alarms differ from it,
    or that no alarm source configures (#475)."""
    ids = [r["point_id"] for r in conn.execute(
        "select distinct point_id from point_limit where basis = 'design' and valid_to is null and record like 'pid:sheet:%'")]
    return [i for i in issues(conn, pack, ids) if i["fault"] == "drawing"] if ids else []


def drawing_checks(conn, sheet: str, pack=None) -> list[dict]:
    """Where one P&ID's alarm and trip table disagrees with the alarms configured in the DCS and AF (#475): the
    `drawing` issues on the points its table rates, by the tag the table names."""
    prefix = f"pid:sheet:{sheet}#"
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    ids = [r["point_id"] for r in conn.execute(
        "select distinct point_id from point_limit where basis = 'design' and record like %s", (like,))]
    if not ids:
        return []
    return [{"tag": i["record"][len(prefix):], "point": i["point"], "point_id": i["point_id"], "type": i["type"],
             "detail": i["detail"]}
            for i in issues(conn, pack, ids) if i["fault"] == "drawing" and i["record"].startswith(prefix)]


def _drawing(rs: list[dict], by_type: dict[str, list], priority: list[str]) -> list[dict]:
    """One point's design limits (a P&ID's alarm and trip table) against its alarms: one issue for the settings that
    differ from the alarm that counts (the first source in priority), one for settings no source configures."""
    design = sorted((r for r in rs if r["basis"] == "design"), key=lambda r: TYPES.index(r["limit_type"]))
    if not design:
        return []
    rank = {s: i for i, s in enumerate(priority)}
    differ, unset = [], []
    for r in design:
        t, v = r["limit_type"], r["value"]
        others = sorted(by_type.get(t, []), key=lambda o: (rank.get(o["source"], len(rank)), o["source"]))
        if not others:
            unset.append(f"{t} {v:g}")
        elif abs(others[0]["value"] - v) > max(0.02 * max(abs(others[0]["value"]), abs(v)), 1e-6):
            rest = "".join(f", {o['source']} {o['value']:g}" for o in others[1:])
            differ.append((t, f"{t} {v:g} where {others[0]['source']} has {others[0]['value']:g}{rest}"))
    r0, out = design[0], []
    base = {"point_id": r0["point_id"], "point": r0["label"], "fault": "drawing", "source": r0["source"],
            "record": r0["record"]}
    if differ:
        out.append({**base, "type": differ[0][0], "detail": "the P&ID shows " + "; ".join(d for _, d in differ)})
    if unset:
        out.append({**base, "type": design[0]["limit_type"],
                    "detail": f"the P&ID shows {', '.join(unset)}, but no alarm source has them"})
    return out


def _peer(description: str | None, uom: str | None) -> tuple:
    words = (description or "").split()
    return (uom or "", " ".join(words[-2:]).lower())


def _missing(conn, point_ids) -> list[dict]:
    """Points with no alarm limits where at least 2 of their peers (same unit of measure and last two words of
    the description, such as "Discharge Temperature"), and at least 3 in 5 of them, have some."""
    pts = conn.execute(
        """
        select e.fuzer_id as id, l.label, max(g.value #>> '{}') filter (where g.field = 'description') as description,
               max(g.value #>> '{}') filter (where g.field = 'uom') as uom
        from entity e join entity_label l using (fuzer_id) left join golden g using (fuzer_id)
        where e.kind = 'timeseries' group by e.fuzer_id, l.label
        """).fetchall()
    with_limits = {r["point_id"] for r in conn.execute(
        "select distinct point_id from point_limit where valid_to is null and basis = 'alarm'")}
    peers: dict[tuple, list] = {}
    for p in pts:
        peers.setdefault(_peer(p["description"], p["uom"]), []).append(p)
    out = []
    wanted = set(point_ids) if point_ids is not None else None
    for key, ps in peers.items():
        have = sum(1 for p in ps if p["id"] in with_limits)
        if have < 2 or have < 0.6 * len(ps):
            continue
        for p in ps:
            if p["id"] not in with_limits and (wanted is None or p["id"] in wanted):
                out.append({"point_id": p["id"], "point": p["label"], "fault": "missing",
                            "detail": f"no alarm limits, while {have} of {len(ps)} '{key[1]}' points have them"})
    return out


# ---- operating modes ------------------------------------------------------------------------------

def units(conn, point_ids: list[str] | None = None) -> dict[str, str]:
    """The unit each point measures: the equipment tag its instrument belongs to, else the tag or well that
    has it."""
    return {r["point"]: r["unit"] for r in conn.execute(
        """
        select distinct on (t.dst) t.dst as point, coalesce(p.dst, t.src) as unit
        from edge t
        join entity s on s.fuzer_id = t.src and s.kind in ('tag', 'well')
        left join lateral (select p.dst from edge p join entity a on a.fuzer_id = p.dst and a.kind = 'tag'
                           where p.src = t.src and p.rel = 'part_of' limit 1) p on true
        where t.rel = 'has_timeseries' and (%(ids)s::text[] is null or t.dst = any(%(ids)s))
        order by t.dst, (p.dst is null), t.src
        """, {"ids": point_ids})}


def run_signals(conn, pack, unit_ids: set[str]) -> dict[str, dict]:
    """Each unit's run signal: {unit: {point (as timeseries._point), on, off}}, the unit's own or the ones
    it follows ({"follow": [units]}) when it has none: units in the same area and train (`graph.structure`, from
    piping and drives), else all units in the area that have one."""
    from ..sources.timeseries import _point

    rules = config(pack)["run_signals"]
    of_unit: dict[str, list[dict]] = {}
    for point, unit in units(conn).items():
        of_unit.setdefault(unit, []).append(point)
    labels = {r["fuzer_id"]: (r["label"], r["uom"]) for r in conn.execute(
        """
        select l.fuzer_id, l.label, (select g.value #>> '{}' from golden g where g.fuzer_id = l.fuzer_id and g.field = 'uom') as uom
        from entity_label l join entity e using (fuzer_id) where e.kind = 'timeseries'
        """)}
    own: dict[str, dict] = {}
    for unit, pts in of_unit.items():
        for rule in rules:
            match = sorted(p for p in pts if p in labels and (
                ("uom" in rule and labels[p][1] == rule["uom"]) or ("point" in rule and re.search(rule["point"], labels[p][0]))))
            if match and (pt := _point(conn, match[0])):
                on = float(rule["above"])
                own[unit] = {"point": pt, "on": on, "off": float(rule.get("below", on / 3))}
                break
    area = {r["src"]: r["dst"] for r in conn.execute(
        "select e.src, e.dst from edge e join entity a on a.fuzer_id = e.dst and a.kind = 'area' "
        "where e.rel in ('part_of', 'located_at') and e.valid_to is null")}
    from ..graph import structure

    plant = structure.load(conn)  # a unit's train from piping, with what drives it (never its tag's name)
    of_train = {m: t for t in set(plant.trains.values()) for m in t | {plant.drives[x] for x in t if x in plant.drives}}
    train = lambda u: of_train.get(u) or of_train.get(plant.drives.get(u, ""))
    out = {}
    for u in unit_ids:
        if u in own:
            out[u] = own[u]
            continue
        near = [v for v in own if area.get(v) and area.get(v) == area.get(u)]
        same = [v for v in near if train(v) == train(u)] if train(u) else []
        if same or near:
            out[u] = {"follow": sorted(same or near)}
    return out | {u: own[u] for u in {v for s in out.values() for v in s.get("follow", [])}}


def modes(conn, pack, unit_ids: set[str], start: datetime, end: datetime, step: int, src=None) -> dict[str, np.ndarray]:
    """Each unit's mode at each step from start to end: running, startup (the pack's `settle_minutes`
    after it starts running, while temperatures and pressures settle), stopped, transition (between
    stopped and running), or unknown (no run signal to tell). The signal is read from `settle_minutes`
    before start, so a unit that started just before the window is still settling in it."""
    from ..sources.timeseries import provider

    src = src or provider()
    sig = run_signals(conn, pack, unit_ids)
    n = int((end - start).total_seconds() // 60 // step) + 1
    settle = int(config(pack).get("settle_minutes", 120) // step)
    state: dict[str, np.ndarray] = {}
    for u, s in sig.items():
        if "point" in s:
            v = _values(src, s["point"], start - timedelta(minutes=settle * step), end, step, n + settle)
            st = np.where(v >= s["on"], "running", np.where(v < s["off"], "stopped", "transition")).astype(object)
            i = np.arange(len(st))
            since_down = i - np.maximum.accumulate(np.where(st != "running", i, -10 ** 9))
            st[(st == "running") & (since_down <= settle)] = "startup"
            state[u] = st[settle:]
    out = {}
    for u in unit_ids:
        s = sig.get(u)
        if s is None:
            out[u] = np.full(n, "unknown", dtype=object)
        elif "point" in s:
            out[u] = state[u].astype(object)
        else:
            group = np.array([state[v] for v in s["follow"]])
            out[u] = np.where((group == "running").any(0), "running",
                              np.where((group == "startup").any(0), "startup",
                                       np.where((group == "stopped").all(0), "stopped", "transition"))).astype(object)
    return out


# ---- learned normal ranges ------------------------------------------------------------------------

def _all_points(conn) -> list[dict]:
    from ..sources.timeseries import _point

    ids = [r["fuzer_id"] for r in conn.execute("select fuzer_id from entity where kind = 'timeseries' order by fuzer_id")]
    return [p for p in (_point(conn, i) for i in ids) if p]


def _values(src, p: dict, start: datetime, end: datetime, step: int, n: int) -> np.ndarray:
    v = np.array([x for _, x in src.series({**p, "upsets": []}, start, end, step)][:n], dtype=float)
    return np.pad(v, (0, n - len(v)), constant_values=np.nan)


def learn(conn, pack, start: str, end: str, step: int = 60, src=None, point_ids: list[str] | None = None) -> dict:
    """Learn each point's normal range per mode of its unit over [start, end] (1st, 50th and 99th
    percentile) into point_normal. A unit with no run signal is learned as `any`. Points matching the
    pack's `limits.not_learned` (positions operators set, such as chokes and valves) are left out."""
    from ..sources.timeseries import provider

    src = src or provider()
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    n = int((b - a).total_seconds() // 60 // step) + 1
    skip = [re.compile(x) for x in config(pack).get("not_learned", [])]
    pts = [p for p in _all_points(conn) if (point_ids is None or p["id"] in point_ids)
           and not any(x.search(p["point"]) or x.search(p["description"] or "") for x in skip)]
    unit = units(conn, [p["id"] for p in pts])
    mode = modes(conn, pack, set(unit.values()), a, b, step, src)
    rows, errors = [], []
    for p in pts:
        try:
            v = _values(src, p, a, b, step, n)
        except Exception as e:  # one unreadable point shouldn't stop the rest
            errors.append(f"{p['point']}: {e}")
            continue
        m = mode.get(unit.get(p["id"]), np.full(n, "unknown", dtype=object))
        known = m != "unknown"
        groups = {k: (m == k) & ~np.isnan(v) for k in ("running", "startup", "stopped", "transition")} if known.any() else \
            {"any": ~np.isnan(v)}
        for k, mask in groups.items():
            if mask.sum() < 12:
                continue
            p01, p50, p99 = np.percentile(v[mask], [1, 50, 99])
            rows.append((p["id"], k, float(p01), float(p50), float(p99), int(mask.sum()), float(mask.mean()), a, b))
    conn.execute("delete from point_normal where %s::text[] is null or point_id = any(%s)", (point_ids, point_ids))
    db.copy_rows(conn, "point_normal", ["point_id", "mode", "p01", "p50", "p99", "n", "share", "since", "until"], rows)
    conn.commit()
    return {"points": len(pts), "ranges": len(rows), "errors": errors[:10], "since": start, "until": end, "step": step}


# ---- excursions -----------------------------------------------------------------------------------

def find(times: list[datetime], values: np.ndarray, running: np.ndarray, limit: dict, step: int) -> list[dict]:
    """Stretches outside one limit ({limit_type, value, valid_from, valid_to}) while running: start, end
    (None if still outside at the last value), peak and minutes outside. As with an alarm's deadband, a
    stretch ends only once the value is back inside by DEADBAND of the limit; back outside within
    MERGE_MINUTES is still the same stretch."""
    high, lim = limit["limit_type"] in HIGH, limit["value"]
    t = np.array(times, dtype="datetime64[m]")
    valid = np.ones(len(times), dtype=bool)
    if limit.get("valid_from"):
        valid &= t >= np.datetime64(limit["valid_from"], "m")
    if limit.get("valid_to"):
        valid &= t < np.datetime64(limit["valid_to"], "m")
    ok = running & valid & ~np.isnan(values)
    band = max(DEADBAND * abs(lim), 1e-6)
    with np.errstate(invalid="ignore"):
        out = ((values > lim) if high else (values < lim)) & ok
        back = ((values < lim - band) if high else (values > lim + band)) | ~ok
    # outside from a crossing until the first value back inside the deadband (or the unit stopping)
    last_out = np.maximum.accumulate(np.where(out, np.arange(len(out)), -1))
    last_back = np.maximum.accumulate(np.where(back, np.arange(len(out)), -1))
    held = (last_out > last_back) & ok
    idx = np.flatnonzero(held)
    if not len(idx):
        return []
    gap = max(1, MERGE_MINUTES // step)
    runs, first, last = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - last > gap:
            runs.append((first, last))
            first = i
        last = i
    runs.append((first, last))
    res = []
    for a, b in runs:
        seg = values[a:b + 1][held[a:b + 1]]
        res.append({"start": times[a], "end": times[b + 1] if b + 1 < len(times) else None,
                    "peak": float(seg.max() if high else seg.min()),
                    "minutes_outside": float(held[a:b + 1].sum() * step)})
    return res


def _limits_to_check(rows: list[dict], normals: dict | None, priority: list[str], a: datetime, b: datetime) -> list[dict]:
    """Every limit that counts at some time in [a, b]: the alarm limit per type chosen at each change of
    the rows' validity, every IOW side, and the learned range for points with neither."""
    out = {}
    cuts = sorted({a, *(r[k] for r in rows for k in ("valid_from", "valid_to") if r[k] and a < r[k] < b)})
    for i, at in enumerate(cuts):
        until = cuts[i + 1] if i + 1 < len(cuts) else None
        e = effective(rows, normals, priority, at)
        for t, x in e["alarm"].items():
            key = (x["record"], t)
            lim = out.setdefault(key, {"record": x["record"], "limit_type": t, "basis": "alarm", "level": x["level"],
                                       "value": x["value"], "allowed_minutes": None, "spans": []})
            lim["spans"].append((at, until))
        for w in e["iow"]:
            for side in ("lo", "hi"):
                if w[side] is not None:
                    lim = out.setdefault((w["record"], side), {"record": w["record"], "limit_type": side, "basis": "iow",
                                                               "level": w["level"], "value": w[side],
                                                               "allowed_minutes": w["allowed_minutes"], "iow": w["iow"],
                                                               "note": w["note"], "spans": []})
                    lim["spans"].append((at, until))
        if (lr := e.get("learned")) and i == 0:
            for side, t in (("lo", "lo"), ("hi", "hi")):
                out[("learned", t)] = {"record": "learned", "limit_type": t, "basis": "learned", "level": None,
                                       "value": lr[side], "allowed_minutes": None, "spans": [(at, None)]}
    flat = []
    for lim in out.values():
        for f, to in lim.pop("spans"):
            flat.append({**lim, "valid_from": f, "valid_to": to})
    return flat


def excursions(conn, pack, point_ids: list[str], start: datetime, end: datetime, step: int = 10, src=None) -> list[dict]:
    """Excursions on these points in [start, end] while their units run (not stored; see scan). Limits
    the checks flag as standing, implausible or out of order are not used."""
    from ..sources.timeseries import _point, provider

    src = src or provider()
    n = int((end - start).total_seconds() // 60 // step) + 1
    times = [start + timedelta(minutes=step * i) for i in range(n)]
    limits, normals = rows_for(conn, point_ids)
    suspect = {(i["point_id"], i.get("source"), i.get("type")) for i in issues(conn, pack, point_ids) if i["fault"] in SUSPECT}
    for pid, rs in limits.items():  # a limit the checks say is wrong would only raise noise
        limits[pid] = [r for r in rs if (pid, r["source"], r["limit_type"]) not in suspect]
    pri = config(pack)["priority"]
    unit = units(conn, point_ids)
    mode = modes(conn, pack, set(unit.values()), start, end, step, src)
    out = []
    for pid in point_ids:
        checks = _limits_to_check(limits.get(pid, []), normals.get(pid), pri, start, end)
        if not checks or not (p := _point(conn, pid)):
            continue
        v = _values(src, p, start, end, step, n)
        m = mode.get(unit.get(pid), np.full(n, "unknown", dtype=object))
        running = (m == "running") | (m == "unknown")
        for lim in checks:
            for x in find(times, v, running, lim, step):
                out.append({"point_id": pid, "point": p["point"], "unit_id": unit.get(pid), **x,
                            **{k: lim.get(k) for k in ("record", "limit_type", "basis", "level", "allowed_minutes", "iow", "note")},
                            "limit_value": lim["value"]})
    return sorted(out, key=lambda x: x["start"])


@detectors.register("iow_breaches", title="Time outside an integrity operating window past its allowed time",
                    kind=detectors.RULE, cadence="request", raises=("iow_breach",), eval="replay:limits",
                    needs={"historian points": detectors.POINTS,
                           "alarm limits, IOWs or learned normal ranges":
                               "select 1 from point_limit union all select 1 from point_normal limit 1"},
                    found=lambda out: len(out.get("work") or []))
def scan(conn, pack, start: str, end: str, step: int = 10, src=None, point_ids: list[str] | None = None,
         open_work: bool = True) -> dict:
    """Find excursions in [start, end] and keep them in the excursion table; one still open from the last
    scan is extended. IOW breaches past their allowed time (critical, standard) open an iow_breach work
    item, one per IOW while it is open (later breaches go to it as evidence)."""
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if point_ids is None:
        point_ids = [r["point_id"] for r in conn.execute(
            "select distinct point_id from point_limit union select distinct point_id from point_normal order by 1")]
    found = excursions(conn, pack, point_ids, a, b, step, src)
    kept, extended, breaches = 0, 0, {}
    for x in found:
        prev = conn.execute(
            """
            select * from excursion where point_id = %s and record = %s and limit_type = %s and "end" is null
              and start < %s order by start desc limit 1
            """, (x["point_id"], x["record"], x["limit_type"], x["start"])).fetchone()
        if prev and x["start"] <= a + timedelta(minutes=step):
            high = x["limit_type"] in HIGH
            row = conn.execute(
                """
                update excursion set "end" = %s, peak = %s, minutes_outside = minutes_outside + %s
                where id = %s returning *
                """, (x["end"], max(prev["peak"], x["peak"]) if high else min(prev["peak"], x["peak"]),
                      x["minutes_outside"], prev["id"])).fetchone()
            extended += 1
        else:
            row = conn.execute(
                """
                insert into excursion (point_id, record, limit_type, basis, level, limit_value, start, "end", peak,
                                       minutes_outside, allowed_minutes)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (point_id, record, limit_type, start) do update
                set "end" = excluded."end", peak = excluded.peak, minutes_outside = excluded.minutes_outside
                returning *
                """, (x["point_id"], x["record"], x["limit_type"], x["basis"], x["level"], x["limit_value"], x["start"],
                      x["end"], x["peak"], x["minutes_outside"], x["allowed_minutes"])).fetchone()
            kept += 1
        if (open_work and row["basis"] == "iow" and row["level"] in WORK_LEVELS and row["work_item_id"] is None
                and row["allowed_minutes"] is not None and row["minutes_outside"] > row["allowed_minutes"]):
            breaches.setdefault(row["record"], []).append((row, x))
    work = [_open_work(conn, rows) for rows in breaches.values()]
    conn.commit()
    return {"since": start, "until": end, "points": len(point_ids), "excursions": kept, "extended": extended,
            "by_basis": dict(Counter(x["basis"] for x in found)), "work": work}


def _open_work(conn, rows: list[tuple[dict, dict]]) -> dict:
    """Raise the IOW's breach exception for its breaches in a scan: its iow_breach process starts, or the open one
    gets them as evidence. The case is remembered on the excursions."""
    from ..core import work

    row, x = max(rows, key=lambda r: r[0]["minutes_outside"])
    hours = sum(r["minutes_outside"] for r, _ in rows) / 60
    iow = x.get("iow") or _iow_id(row["record"])
    data = {"iow": iow, "record": row["record"], "point": x["point"], "point_id": row["point_id"],
            "unit_id": x.get("unit_id"), "side": row["limit_type"], "level": row["level"], "limit": row["limit_value"],
            "allowed_minutes": row["allowed_minutes"], "note": x.get("note"), "breaches": len(rows),
            "hours_outside": round(hours, 1),
            "peak": (max if row["limit_type"] in HIGH else min)(r["peak"] for r, _ in rows),
            "excursions": [{"id": r["id"], "start": r["start"].isoformat(), "end": _iso(r["end"]), "peak": r["peak"],
                            "minutes_outside": r["minutes_outside"]} for r, _ in rows]}
    title = (f"IOW {iow} {row['level']} breach on {x['point']}: {'above' if row['limit_type'] in HIGH else 'below'} "
             f"{row['limit_value']:g}, {len(rows)} time{'s' if len(rows) > 1 else ''} past the allowed "
             f"{row['allowed_minutes'] / 60:g} h")
    ids = [r["id"] for r, _ in rows]
    uom = (conn.execute("select value #>> '{}' as uom from golden where fuzer_id = %s and field = 'uom'",
                        (row["point_id"],)).fetchone() or {}).get("uom")
    try:
        res = raise_exception(conn, IOW_BREACH, iow, title, "agent:sre", source="limits",
                              refs=[s for s in (row["point_id"], x.get("unit_id")) if s],
                              priority=WORK_LEVELS[row["level"]], finding=Finding(
                                  "limits.iow", DETERMINISTIC, standard="API RP 584", data=data,
                                  method="Time outside an integrity operating window, past its allowed time",
                                  start=min(r["start"] for r, _ in rows),
                                  end=None if any(r["end"] is None for r, _ in rows) else max(r["end"] for r, _ in rows),
                                  items=(fact("Window", iow), asset("Measured on", row["point_id"]),
                                         asset("Unit", x.get("unit_id")), fact("Level", row["level"]),
                                         fact("Limit", row["limit_value"], uom),
                                         fact("Side", "above" if row["limit_type"] in HIGH else "below"),
                                         fact("Peak", data["peak"], uom), fact("Breaches", len(rows)),
                                         fact("Time outside", data["hours_outside"], "h"),
                                         fact("Allowed", round(row["allowed_minutes"] / 60, 2), "h"),
                                         moment("First outside", min(r["start"] for r, _ in rows)),
                                         fact("Note", x.get("note")))))
    except (work.WorkError, ExceptionError) as e:
        return {"iow": iow, "excursions": ids, "error": str(e)}
    conn.execute("update excursion set work_item_id = %s where id = any(%s)", (res["item_id"], ids))
    return {"iow": iow, "excursions": ids, "item_id": res["item_id"], "started": res["raised"], "title": title,
            "exception_id": res["id"]}


def _excursion_out(r: dict) -> dict:
    return {"start": _iso(r["start"]), "end": _iso(r["end"]), "limit_type": r["limit_type"], "basis": r["basis"],
            "level": r["level"], "limit": r["limit_value"], "peak": r["peak"], "minutes_outside": r["minutes_outside"],
            "allowed_minutes": r["allowed_minutes"], "record": r["record"], "work_item_id": r["work_item_id"]}
