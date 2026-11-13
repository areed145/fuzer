"""Trend views (P5.12): an entity's process values grouped by its equipment class's template, so a compressor opens on
its pressures, temperatures, driver and vibration together instead of one chart per tag.

    view(conn, fuzer_id, at=None, hours_before=48, hours_after=24) -> {"template", "groups", "other", "missing", ...}

The entity is read on its unit (`values.unit_of`). The unit's ISO 14224 classes pick a template: its bundle's, else
the platform's in `core/trend_templates.yaml` (`core/trends.py`), else the generic one, grouped by what each point
measures. Each trace in a group is a measurement role (P1.4) on the unit, or on a neighbour the template names: its driver, what it drives, what is
upstream or downstream of it. Points on the unit that no group asked for go to `other`, switched off, so nothing is
hidden. Roles the template expects and the unit has no point for are listed in `missing`.

Sparse readings landed from other sources (well tests, lab samples, inspection readings: `graph.measurements`) are one
more group, `tests`, each a stepped series with a marker per reading, carrying the reading before the window in and
the one after it out. It starts switched on when a reading falls in the window (P5.13d).

Traces in a group that share units share an axis (`axis`). Values come from the time-series provider through
`values.around`, so the window, the centre on the latest incident, the limits and the provider label are the same as
the entity page's.
"""

from ..core import roles as vocabulary
from ..core import trends
from ..core.trends import parse
from ..graph import measurements as landed
from ..graph import roles as point_roles
from ..sources.timeseries import _point, _unit_points
from . import values

RELATIONS = {
    "self": None,
    "driver": "select src as id from edge where rel = 'drives' and dst = %(u)s and valid_to is null",
    "driven": "select dst as id from edge where rel = 'drives' and src = %(u)s and valid_to is null",
    "upstream": "select src as id from edge where rel in ('pipes_to', 'flows_to') and dst = %(u)s and valid_to is null",
    "downstream": "select dst as id from edge where rel in ('pipes_to', 'flows_to') and src = %(u)s and valid_to is null",
}
MAX_TRACES = 40  # what one chart reads at once
PER_ENTRY = 4  # points one trace entry may bring (two upstream vessels, two bearing temperatures, ...)


def templates(pack=None) -> dict:
    """The templates this asset uses: the platform's, with its bundle's on top (core/trends.py)."""
    return trends.for_pack(pack)


def pick(classes: set[str], pack=None) -> tuple[str, dict] | None:
    """The template for a unit of these classes, in a stable order; None for the generic one."""
    own = templates(pack)["classes"]
    return next(((c, own[c]) for c in sorted(classes) if c in own), None)


def _neighbours(conn, unit: str) -> dict[str, list[str]]:
    """The assets each relation names, for this unit. `self` is the unit and the equipment installed at it."""
    out = {"self": [unit] + [r["src"] for r in conn.execute(
        "select src from edge where dst = %s and rel = 'installed_at' and valid_to is null order by src", (unit,))]}
    for name, sql in RELATIONS.items():
        if sql:
            out[name] = sorted({r["id"] for r in conn.execute(sql, {"u": unit})})
    return out


def _labels(conn, ids) -> dict[str, str]:
    return {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (list(ids),))}


def _by_role(conn, assets: list[str]) -> dict[tuple[str, str], list[str]]:
    """(asset, role) -> its points, best proposal first."""
    out: dict[tuple[str, str], list[str]] = {}
    for r in conn.execute(
            "select asset, role, point_id from measurement_role where asset = any(%s) "
            "order by confidence desc, point_id", (assets,)):
        out.setdefault((r["asset"], r["role"]), []).append(r["point_id"])
    return out


def _axis(uom: str | None) -> str:
    return (uom or "").strip().lower() or "value"


def _template_groups(conn, unit: str, template: dict) -> tuple[list[dict], list[dict]]:
    """The template's groups as traces (point id, role, the asset it is on and how), and the expected roles found
    nowhere."""
    near = _neighbours(conn, unit)
    found = _by_role(conn, sorted({a for ids in near.values() for a in ids}))
    labels = _labels(conn, {a for ids in near.values() for a in ids})
    seen: set[str] = set()
    groups = []
    for g in template["groups"]:
        traces = []
        for entry in g["traces"]:
            relation, role = parse(entry)
            for asset in near[relation]:
                for pid in found.get((asset, role), [])[:PER_ENTRY]:
                    if pid in seen:
                        continue
                    seen.add(pid)
                    traces.append({"id": pid, "role": role, "role_label": vocabulary.ROLES[role].label,
                                   "on": {"id": asset, "label": labels.get(asset), "relation": relation}})
        groups.append({"key": g["key"], "label": g["label"], "show": g.get("show", True), "traces": traces})
    missing = []
    for alternatives in template.get("expect", []):
        if not any(found.get((a, parse(e)[1])) for e in alternatives for a in near[parse(e)[0]]):
            relation, role = parse(alternatives[0])
            missing.append({"role": role, "role_label": vocabulary.ROLES[role].label, "relation": relation})
    return groups, missing


def _generic_groups(conn, point_ids: list[str]) -> list[dict]:
    """Points grouped by what they measure, in the generic template's order, the first few shown."""
    generic = trends.builtin()["generic"]
    have = point_roles.of(conn, point_ids)
    by: dict[str, list[dict]] = {}
    for pid in point_ids:
        role = have.get(pid)
        quantity = vocabulary.ROLES[role].quantity if role in vocabulary.ROLES else None
        by.setdefault(quantity or "other", []).append(
            {"id": pid, "role": role, "role_label": vocabulary.ROLES[role].label if role in vocabulary.ROLES else None,
             "on": None})
    order = [q for q in generic["quantities"] if q in by] + [q for q in by if q not in generic["quantities"]]
    return [{"key": q, "label": "Other" if q == "other" else q.replace("_", " ").capitalize(),
             "show": i < generic["shown"], "traces": by[q]} for i, q in enumerate(order)]


LOOP_TRACES = {"pv": "Process value", "sp": "Setpoint", "op": "Controller output", "mode": "Mode (1 auto, 0 manual)"}


def _loop_groups(conn, loop_id: str) -> list[dict]:
    """A control loop's four points as one group: the setpoint on the process value's axis, the output and the mode
    each on their own, so a level held at 50 % reads in % and not on the mode's 0 to 1."""
    from .control_loops import loop

    pts = loop(conn, loop_id)["_series"]
    axes = {"pv": "pv", "sp": "pv", "op": "op", "mode": "mode"}
    traces = [{"id": pts[r]["id"], "role": None, "role_label": label, "on": None, "axis": axes[r]}
              for r, label in LOOP_TRACES.items() if pts.get(r)]
    return [{"key": "loop", "label": "Control loop", "show": True, "traces": traces}]


def _tests(conn, unit: str, start: str, end: str) -> dict | None:
    """The unit's landed readings as one group of stepped series: the readings in [start, end], with the one before and
    the one after so a monthly well test reads across the window. None when it has none near the window."""
    traces = []
    for s in landed.for_entity(conn, unit)["series"]:
        vals = [(v["at"][:16], v["value"], v["method"]) for v in s["values"]]
        inside = [v for v in vals if start <= v[0] <= end]
        before = [v for v in vals if v[0] < start][-1:]
        after = [v for v in vals if v[0] > end][:1]
        if not inside and not (before and after):
            continue
        shown = [v[1] for v in before + inside + after]
        name = s["quantity"].replace("_", " ").capitalize() + (f" at {s['location']}" if s["location"] else "")
        traces.append({"id": f"m:{s['of']['id']}|{s['quantity']}|{s['location'] or ''}", "point": name,
                       "description": name, "uom": s["unit"], "role": None, "role_label": name,
                       "on": {"id": s["of"]["id"], "label": s["of"]["label"], "relation": "self"},
                       "link": s["of"]["id"], "axis": _axis(s["unit"]), "sampled": True, "inside": len(inside),
                       "values": [[at, v] for at, v, _ in before + inside + after],
                       "methods": [m for _, _, m in before + inside + after],
                       "min": min(shown), "max": max(shown), "mean": sum(shown) / len(shown), "last": shown[-1],
                       "limits": []})
    if not traces:
        return None
    show = any(t["inside"] for t in traces)
    return {"key": "tests", "label": "Tests and samples", "show": show,
            "traces": [{**t, "show": show and bool(t["inside"])} for t in traces[:MAX_TRACES]]}


def view(conn, fuzer_id: str, at: str | None = None, hours_before: int = 48, hours_after: int = 24,
         src=None, pack=None) -> dict:
    """The entity's trend, grouped by its class's template, with every trace's values in the window."""
    row, unit = values.unit_of(conn, fuzer_id)
    if not row:
        return {"error": "unknown entity"}
    if row["kind"] == "timeseries":  # one point: its own trend, nothing to group
        classes, chosen = set(), None
        groups, missing = _generic_groups(conn, [fuzer_id]), []
        others: list[str] = []
    elif row["kind"] == "control_loop":  # its process value, setpoint, output and mode together
        classes, chosen = set(), None
        groups, missing, others = _loop_groups(conn, fuzer_id), [], []
    else:
        classes = point_roles.classes(conn, unit) | ({"WELL"} if _kind(conn, unit) == "well" else set())
        chosen = pick(classes, pack)
        mine = [p["id"] for p in _unit_points(conn, unit, MAX_TRACES * 2)]
        if chosen:
            groups, missing = _template_groups(conn, unit, chosen[1])
            taken = {t["id"] for g in groups for t in g["traces"]}
            others = [p for p in mine if p not in taken]
        else:
            groups, missing, others = _generic_groups(conn, mine), [], []
    budget = MAX_TRACES
    for g in groups:  # the template's order decides what is read when there are too many
        g["traces"] = g["traces"][:max(0, budget)]
        budget -= len(g["traces"])
    others = others[:max(0, budget)]
    ids = [t["id"] for g in groups for t in g["traces"]] + others
    points = [p for p in (_point(conn, i) for i in ids) if p]
    read = values.around(conn, fuzer_id, at, hours_before, hours_after, src=src, pack=pack, points=points)
    if "error" in read:
        return read
    series = {s["id"]: s for s in read.pop("series")}
    for g in groups:
        g["traces"] = [{**series[t["id"]], **t, "axis": t.get("axis") or _axis(series[t["id"]]["uom"]), "show": g["show"]}
                       for t in g["traces"] if t["id"] in series]
    other = [{**series[i], "axis": _axis(series[i]["uom"]), "show": False} for i in others if i in series]
    tests = _tests(conn, unit, read["start"], read["end"]) if row["kind"] != "timeseries" else None
    if tests:
        groups.append(tests)
    return {**read, "entity": fuzer_id, "unit_label": _labels(conn, [unit]).get(unit), "classes": sorted(classes),
            "template": {"key": chosen[0] if chosen else "generic",
                         "title": chosen[1]["title"] if chosen else trends.builtin()["generic"]["title"],
                         "source": chosen[1]["source"] if chosen else "platform"},
            "groups": [g for g in groups if g["traces"]], "other": other, "missing": missing}


def _kind(conn, fid: str) -> str | None:
    r = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()
    return r["kind"] if r else None
