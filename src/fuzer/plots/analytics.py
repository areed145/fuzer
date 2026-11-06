"""Live figures: charts built on request from the latest records, for any page to show.

A live figure is a complete panel, so a page needs no knowledge of the chart. Beside the Plotly spec,
its summary carries everything worded about it (headline, context, stats, notes, warnings, tables),
always from the data in hand. As elsewhere in fuzer.plots the spec holds no colours: series take the
page's colorway by trace position, so each series keeps a fixed position even when it has no data.
To add one, write a builder and register it in FIGURES.
"""

from html import escape

from ..analysis import flow_capacity as fc
from ..analysis import production_efficiency as pe
from . import ChartError, Figure, check, layout

LIVE_NOTICE = "Latest recorded data, not a live operating update."
HEIGHT = 340
UNKNOWN = "Unknown"


def _number(value: float | None) -> str:
    return UNKNOWN if value is None else f"{value:,.1f}".removesuffix(".0")


def _rate(value: float | None) -> str:
    return UNKNOWN if value is None else f"{_number(value)} Sm3/d"


def _compact(value: float) -> str:
    return f"{value / 1000:.1f}k".replace(".0k", "k") if value >= 1000 else f"{value:.0f}"


def _words(value: str) -> str:
    return value.replace("_", " ")


def constraint_label(point: dict, constraints: list[dict]) -> str:
    """What bound a day, or why that is not known. Ties name every binding constraint."""
    ids = sorted({*point["binding_constraint_ids"], *([point["active_constraint_id"]] if point["active_constraint_id"] else [])})
    known = {c["id"]: c for c in constraints}
    if point["dynamic_max_sm3d"] is None or point["capacity_basis"] == "missing" or not set(ids) <= set(known):
        return UNKNOWN
    if point["capacity_basis"] == "reported_potential":
        return "Reported potential · physical constraint unknown"
    if not ids:
        return UNKNOWN
    labels = " + ".join(f"{known[id]['label']} [{known[id]['category']}]" for id in ids)
    return f"{labels} (tied)" if len(ids) > 1 else labels


def production_efficiency(out: dict, notice: str) -> Figure:
    """Allocated oil against the daily dynamic maximum. Unknown days stay gaps, never zero."""
    points, constraints, summary = out["points"], out["constraints"], out["summary"]
    days = [p["date"] for p in points]

    def hover(p: dict, field: str, label: str) -> str:
        lines = [f"{label}: {_rate(p[field])}"]
        if field == "allocated_sm3d":
            lines.append(f"Efficiency: {_number(p['efficiency_pct'])} %")
        if field == "dynamic_max_sm3d":
            lines += [f"Binding: {constraint_label(p, constraints)}", f"Capacity basis: {_words(p['capacity_basis'])}",
                      f"Flags: {', '.join(p['flags']) or 'none'}"]
        return "<br>".join(escape(line) for line in lines)

    data = []
    for field, label, extra in [("dynamic_max_sm3d", "Dynamic maximum", {}), ("allocated_sm3d", "Allocated production", {}),
                                ("gap_sm3d", "Unrealized gap", {"visible": "legendonly"})]:
        data.append({"type": "scatter", "mode": "lines+markers", "name": f"{label} (Sm3/d)", "x": days,
                     "y": [p[field] for p in points], "connectgaps": False, "marker": {"size": 8}, "line": {"width": 2},
                     "text": [hover(p, field, label) for p in points], "hovertemplate": "%{text}<extra></extra>", **extra})
    efficiency = summary["efficiency_pct"]
    rows = [[p["date"], _number(p["dynamic_max_sm3d"]), _number(p["allocated_sm3d"]), _number(p["gap_sm3d"]),
             _number(p["efficiency_pct"]), constraint_label(p, constraints),
             _words(p["capacity_basis"]) + (f" · {', '.join(p['flags'])}" if p["flags"] else "")] for p in points]
    return Figure(
        kind="production-efficiency", title="Production efficiency", data=data,
        layout=layout("", height=HEIGHT, hovermode="x unified", margin={"t": 12, "r": 16, "b": 60, "l": 64},
                      xaxis={"title": {"text": "Production day (UTC)"}, "type": "date", "tickformat": "%d %b"},
                      yaxis={"title": {"text": "Oil rate (Sm3/d)"}, "rangemode": "tozero"},
                      legend={"orientation": "h", "y": -0.28, "x": 0, "font": {"size": 11}},
                      uirevision=f"{out['facility']['id']}:{out['end']}"),
        summary={
            "context": f"{out['facility']['label']} · {days[0]} to {out['end']} inclusive · {notice}",
            "headline": None if summary["paired_days"] else "No paired production and capacity values in this window.",
            "stats": [
                {"label": "Weighted efficiency", "value": "Unknown" if efficiency is None else f"{_number(efficiency)}%"},
                {"label": "Allocated · paired days", "value": _number(summary["allocated_sm3"]), "unit": "Sm3"},
                {"label": "Dynamic maximum · paired days", "value": _number(summary["dynamic_max_sm3"]), "unit": "Sm3"},
                {"label": "Paired days", "value": f"{summary['paired_days']} / {len(points)}"},
            ],
            "notes": ["Unknown values are gaps, never zero."],
            "warnings": out["warnings"],
            "tables": [{"title": "Daily data", "columns": [
                "Day", "Maximum (Sm3/d)", "Allocated (Sm3/d)", "Gap (Sm3/d)", "Efficiency (%)", "Binding constraints",
                "Capacity basis / flags"], "rows": rows}],
            "source": out["source"],
        },
        sources=sorted({id for p in points for id in p["source_ids"]}),
    )


def _system_label(system: dict) -> str:
    if system["established"]:
        return "System bottleneck constraint"
    if system["basis"] == "declared_limits":
        return "Lowest declared bound (coverage incomplete)"
    return "Reported potential (constraint unknown)" if system["basis"] == "reported_potential" else "System limit unknown"


def _verdict(out: dict) -> str:
    system = out["system"]
    binding = " + ".join(system["binding_stages"]) or UNKNOWN
    if system["established"]:
        return f"Bottleneck: {binding} at {_rate(system['limit_sm3d'])}. Every stage has a capacity."
    if system["basis"] == "declared_limits":
        return (f"Lowest declared bound is {binding} at {_rate(system['limit_sm3d'])}, but some stages have no "
                "capacity, so the bottleneck is not established.")
    return "Bottleneck not established: no stage has a declared capacity, so available capacity is unknown."


# (key, legend, label prefix, Plotly pattern). Order is colorway position, so a series keeps its colour.
SERIES = [
    ("capacity", "Approved capacity or reported well potential", "", None),
    ("limit", None, "", None),
    ("design", "Original design value, not revisited", "", "."),
    ("demonstrated", "Demonstrated peak: capacity is at least this", "≥", "/"),
    ("well_tests", "Latest well tests, not capacity", "", "x"),
]


def _series(stage: dict) -> str | None:
    if stage["basis"] in ("declared", "reported_potential"):
        return "design" if stage["grade"] == "design" else "capacity"
    return None if stage["basis"] == "unknown" else stage["basis"]


def _stage_hover(stage: dict, out: dict) -> str:
    tested = f"sum of latest well tests, {out['well_tests']['wells']} wells"
    basis = tested if stage["basis"] == "well_tests" else stage["grade"] or _words(stage["basis"])
    lines = [stage["label"], f"{'At least' if stage['basis'] == 'demonstrated' else 'Capacity'}: {_rate(stage['capacity_sm3d'])}",
             f"Basis: {basis}"]
    if stage["undeclared"]:
        lines.append(f"Entities: {stage['members']}, {stage['undeclared']} without a capacity")
    if stage["binding"]:
        lines.append("Binding on this day")
    return "<br>".join(escape(line) for line in lines)


def _notes(out: dict) -> list[str]:
    stages, day, peak, tests = out["stages"], out["as_of"], out["demonstrated"], out["well_tests"]
    grades = {s["grade"] for s in stages if s["grade"]}
    notes = []
    if grades:
        notes.append(f"Evidence: {', '.join(sorted(grades))}. Each rating is converted to the oil the facility could "
                     f"carry on {day}, using that day's water cut and gas per oil.")
    notes.append(f"{_system_label(out['system'])}: {_rate(out['system']['limit_sm3d'])} on {day}, the same maximum as "
                 f"the production chart. Allocated that day: {_rate(out['system']['allocated_sm3d'])}.")
    if any(s["basis"] == "demonstrated" for s in stages) and peak["peak_sm3d"] is not None:
        notes.append(f"≥ shows the highest allocated day from {peak['since']} to {day}: {_rate(peak['peak_sm3d'])} on "
                     f"{peak['date']}. The facility delivered that through this network, so each stage allows at "
                     "least this much. It is not a rating.")
    if any(s["basis"] == "well_tests" for s in stages):
        count = tests["not_tested"]
        untested = f" {count} {'well has' if count == 1 else 'wells have'} no test since {tests['since']}." if count else ""
        notes.append(f"Wells show the sum of each well's latest test, {tests['first']} to {tests['last']}: "
                     f"{_rate(tests['total_sm3d'])} across {tests['wells']} wells, unreconciled to allocation.{untested} "
                     "A test rate is not unconstrained well potential.")
    return notes


def flow_capacity(out: dict, notice: str) -> Figure:
    """Capacity per flow stage beside the system limit, on the latest report day."""
    stages, system = out["stages"], out["system"]
    labels = [escape(s["label"]) for s in stages]
    limit = system["limit_sm3d"]
    data = []
    for key, name, prefix, pattern in SERIES:
        if key == "limit":
            y = [limit] * len(stages)
            text = ["" if limit is None else _compact(limit)] * len(stages)
            hover = [f"{escape(_system_label(system))}: {_rate(limit)}"] * len(stages)
            name = _system_label(system)
        else:
            y = [s["capacity_sm3d"] if _series(s) == key else None for s in stages]
            text = ["" if v is None else f"{'◆ ' if s['binding'] else ''}{prefix}{_compact(v)}" for v, s in zip(y, stages)]
            hover = [_stage_hover(s, out) for s in stages]
        data.append({"type": "bar", "name": name, "x": labels, "y": y, "offsetgroup": "system" if key == "limit" else "stage",
                     "showlegend": any(v is not None for v in y), "text": text, "textposition": "outside",
                     "constraintext": "none", "cliponaxis": False, "hovertext": hover, "hovertemplate": "%{hovertext}<extra></extra>",
                     **({"marker": {"pattern": {"shape": pattern}}} if pattern else {})})
    missing = [s for s in stages if s["undeclared"]]
    tables = []
    if missing:
        tables.append({
            "title": f"Metadata needed to establish the bottleneck · {len(missing)} of {len(stages)} stages incomplete",
            "note": f"For each stage: {out['required_metadata']}.",
            "columns": ["Stage", "Without capacity", "Entities"],
            "rows": [[s["label"], f"{s['undeclared']} of {s['members']}",
                      ", ".join(s["undeclared_examples"]) + (", …" if s["undeclared"] > len(s["undeclared_examples"]) else "")]
                     for s in missing],
        })
    return Figure(
        kind="flow-capacity", title="System capacity and bottleneck", data=data if stages else [],
        layout=layout("", height=HEIGHT, barmode="group", bargap=0.28, bargroupgap=0.06, meta={"horizontal_below": 560},
                      xaxis={"title": {"text": "Asset components (sequential flow)"}, "tickangle": -18, "fixedrange": True},
                      yaxis={"title": {"text": "Capacity (Sm3/d oil equivalent)"}, "rangemode": "tozero", "fixedrange": True},
                      margin={"t": 24, "r": 16, "b": 60, "l": 64},
                      legend={"orientation": "h", "x": 0, "y": 1.14, "font": {"size": 11}},
                      uirevision=f"{out['facility']['id']}:{out['as_of']}"),
        summary={
            "context": f"{out['facility']['label']} · latest report {out['as_of']}, routing in force that day · {notice}",
            "headline": _verdict(out) if stages else "No flow network reaches this facility on its latest report day.",
            "stats": [], "notes": _notes(out) if stages else [], "warnings": out["warnings"], "tables": tables,
            "source": out["source"],
        },
    )


def _production(conn, facility: str, notice: str) -> Figure:
    return production_efficiency(pe.production_efficiency(conn, facility), notice)


def _capacity(conn, facility: str, notice: str) -> Figure:
    return flow_capacity(fc.flow_capacity(conn, facility), notice)


FIGURES = {"production-efficiency": _production, "flow-capacity": _capacity}


def live(conn, pack, kind: str, facility: str) -> dict:
    if kind not in FIGURES:
        raise ChartError(f"no live figure {kind!r}")
    fig = FIGURES[kind](conn, facility, pack.data_notice or LIVE_NOTICE)
    check(fig)
    return {"kind": fig.kind, "title": fig.title, "spec": fig.spec, "summary": fig.summary, "sources": fig.sources}
