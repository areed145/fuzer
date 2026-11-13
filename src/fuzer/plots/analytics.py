"""Live figures: charts built on request from the latest records, for any page to show.

A live figure is a complete panel, so a page needs no knowledge of the chart. Beside the Plotly spec,
its summary carries everything worded about it (headline, context, stats, notes, warnings, tables),
always from the data in hand. As elsewhere in fuzer.plots the spec holds no colours: series take the
page's colorway by trace position, so each series keeps a fixed position even when it has no data.
To add one, write a builder and register it in FIGURES.
"""

from datetime import date
from html import escape

from ..analysis import flow_capacity as fc
from ..analysis import production_efficiency as pe
from ..graph import positions
from ..graph.operations import turnaround_scope
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

    # At a near-100% day the two rate lines overlap on a zero-based axis (#307), so the maximum is dashed (it shows
    # through allocated in either theme) and the gap gets its own fitted axis on the right, where its changes show.
    data = []
    for field, label, extra in [("dynamic_max_sm3d", "Dynamic maximum", {"line": {"width": 2, "dash": "dash"}}),
                                ("allocated_sm3d", "Allocated production", {}),
                                ("gap_sm3d", "Unrealized gap", {"yaxis": "y2", "mode": "lines", "line": {"width": 1.5, "dash": "dot"}})]:
        data.append({"type": "scatter", "mode": "lines+markers", "name": f"{label} (Sm3/d)", "x": days,
                     "y": [p[field] for p in points], "connectgaps": False, "marker": {"size": 8}, "line": {"width": 2},
                     "text": [hover(p, field, label) for p in points], "hovertemplate": "%{text}<extra></extra>", **extra})
    efficiency = summary["efficiency_pct"]
    gaps = [p for p in points if p["gap_sm3d"] is not None]
    lost = sum(p["gap_sm3d"] for p in gaps) if gaps else None
    by_cause: dict[str, float] = {}
    for p in gaps:
        by_cause[constraint_label(p, constraints)] = by_cause.get(constraint_label(p, constraints), 0) + p["gap_sm3d"]
    cause = max(by_cause, key=by_cause.get) if lost else None
    rows = [[p["date"], _number(p["dynamic_max_sm3d"]), _number(p["allocated_sm3d"]), _number(p["gap_sm3d"]),
             _number(p["efficiency_pct"]), constraint_label(p, constraints),
             _words(p["capacity_basis"]) + (f" · {', '.join(p['flags'])}" if p["flags"] else "")] for p in points]
    return Figure(
        kind="production-efficiency", title="Production efficiency", data=data,
        layout=layout("", height=HEIGHT, hovermode="x unified", margin={"t": 12, "r": 64, "b": 60, "l": 64},
                      xaxis={"title": {"text": "Production day (UTC)"}, "type": "date", "tickformat": "%d %b"},
                      yaxis={"title": {"text": "Oil rate (Sm3/d)"}, "rangemode": "tozero"},
                      yaxis2={"title": {"text": "Gap (Sm3/d)"}, "overlaying": "y", "side": "right", "showgrid": False},
                      legend={"orientation": "h", "y": -0.28, "x": 0, "font": {"size": 11}},
                      uirevision=f"{out['facility']['id']}:{out['end']}"),
        summary={
            "context": f"{out['facility']['label']} · {days[0]} to {out['end']} inclusive · {notice}",
            "headline": None if summary["paired_days"] else "No paired production and capacity values in this window.",
            "stats": [
                # lead with what was lost and why (#307): at ~100% efficiency the totals alone say little
                {"label": "Unrealized gap · paired days", "value": _number(lost), "unit": "Sm3"},
                *([{"label": "Biggest cause", "value": cause}] if cause else []),
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


def _short(day: str | None) -> str:
    d = date.fromisoformat(day) if day else None
    return f"{d.day} {d:%b %Y}" if d else UNKNOWN


def turnaround_progress(schedule: dict | None, revision: str | None, notice: str) -> Figure:
    """A turnaround's S-curve (#360): percent of the work planned done by each week against the baseline and
    against today's dates, with the percent earned today; slippage named, rows that can't be used listed."""
    c = (schedule or {}).get("curve") or {}
    pts = c.get("points") or []
    days = [p["day"] for p in pts]
    data = []
    if pts:
        data = [
            {"type": "scatter", "mode": "lines", "name": "Planned (baseline)", "x": days, "y": [p["planned"] for p in pts],
             "line": {"width": 2}, "hovertemplate": "%{x}: %{y}% planned<extra></extra>"},
            {"type": "scatter", "mode": "lines", "name": "Forecast (today's dates)", "x": days, "y": [p["forecast"] for p in pts],
             "line": {"width": 2, "dash": "dash"}, "hovertemplate": "%{x}: %{y}% forecast<extra></extra>"},
            {"type": "scatter", "mode": "markers", "name": "Earned", "x": [schedule["as_of"]], "y": [c["earned"]],
             "marker": {"size": 10}, "hovertemplate": "%{x}: %{y}% earned<extra></extra>"},
        ]
    if not schedule:
        headline = f"No planning system's schedule names {revision}." if revision else "No turnaround is chosen."
    elif not pts:
        headline = c.get("basis") or "The schedule has no activities to plot."
    else:
        v, slip = c["variance"], c["slip_days"]
        pace = "on the plan" if abs(v) < 0.5 else f"{abs(v):g} points {'ahead of' if v > 0 else 'behind'} the plan"
        finish = ("finishing on its baseline" if slip == 0 else
                  f"forecast to finish {abs(slip)} days {'after' if slip > 0 else 'before'} its baseline")
        headline = (f"No work is planned done by {_short(schedule['as_of'])}; the work is {finish}."
                    if c["earned"] == 0 and c["planned_today"] == 0
                    else f"{c['earned']:g}% earned against {c['planned_today']:g}% planned: {pace}, {finish}.")
    acts = (schedule or {}).get("activities") or []
    slipped = sorted((a for a in acts if a["slipped_days"] > 0), key=lambda a: -a["slipped_days"])
    tables = [{"title": f"Slipped activities · {len(slipped)}", "note": "Each activity whose finish has moved past its baseline.",
               "columns": ["Activity", "Description", "Baseline finish", "Finish", "Days slipped", "% complete"],
               "rows": [[a["activity"], a["description"] or "", _short(a["baseline_finish"]), _short(a["finish"]),
                         str(a["slipped_days"]), f"{a['percent_complete']:g}"] for a in slipped]}] if slipped else []
    return Figure(
        kind="turnaround-progress", title=f"Turnaround {revision or ''} progress".replace("  ", " "), data=data,
        layout=layout("", height=HEIGHT, xaxis={"type": "date", "fixedrange": True},
                      yaxis={"title": {"text": "Work done (%)"}, "range": [0, 105], "fixedrange": True},
                      margin={"t": 24, "r": 16, "b": 60, "l": 56}, legend={"orientation": "h", "x": 0, "y": 1.14, "font": {"size": 11}},
                      uirevision=f"{revision}:{(schedule or {}).get('as_of')}"),
        summary={
            "context": f"Schedule as of {_short((schedule or {}).get('as_of'))} · {c.get('used', 0)} activities counted · {notice}",
            "headline": headline,
            "stats": [{"label": "Earned", "value": f"{c['earned']:g}", "unit": "%"},
                      {"label": "Planned by now", "value": f"{c['planned_today']:g}", "unit": "%"},
                      {"label": "Baseline finish", "value": _short(c["baseline_finish"]), "unit": None},
                      {"label": "Forecast finish", "value": _short(c["forecast_finish"]), "unit": None}] if pts else [],
            "notes": [c["basis"]] if pts else [],
            "warnings": [f"Not counted: {b['activity']}, {b['why']}." for b in c.get("left_out") or []],
            "tables": tables, "source": (schedule or {}).get("source") or "the planning system's schedule (activities)",
        },
    )


LOG_TRACKS = 2  # depth log curves drawn beside the section (#901)


def well_trajectory(depths: dict, label: str, notice: str) -> Figure:
    """A well's vertical section (#61): true vertical depth against distance out from the wellhead along the hole,
    from its directional survey, with each completion component drawn over its measured depth range, and its first
    depth log curves (LAS, #901) in tracks beside it on the same depth axis."""
    traj, comps, s = depths["trajectory"], depths["components"], depths["summary"]
    out = [round((t["north_m"] ** 2 + t["east_m"] ** 2) ** 0.5, 1) for t in traj]
    data = []
    if traj:
        data.append({"type": "scatter", "mode": "lines", "name": "Hole (survey)", "x": out, "y": [t["tvd_m"] for t in traj],
                     "customdata": [t["md_m"] for t in traj], "line": {"width": 2},
                     "hovertemplate": "MD %{customdata:,.0f} m: TVD %{y:,.0f} m, %{x:,.0f} m out<extra></extra>"})
        mds = [t["md_m"] for t in traj]

        def along(md: float) -> float | None:
            for a, b, xa, xb in zip(mds, mds[1:], out, out[1:]):
                if a <= md <= b:
                    return round(xa + (md - a) / (b - a) * (xb - xa), 1) if b > a else xa
            return None

        xs, ys, names = [], [], []
        for c in comps:
            if c["top_md_m"] is None or c["base_md_m"] is None or c["top_tvd_m"] is None or c["base_tvd_m"] is None:
                continue
            xa, xb = along(c["top_md_m"]), along(c["base_md_m"])
            if xa is None or xb is None:
                continue
            xs += [xa, xb, None]
            ys += [c["top_tvd_m"], c["base_tvd_m"], None]
            names += [c["description"]] * 2 + [None]
        if xs:
            data.append({"type": "scatter", "mode": "lines+markers", "name": "Completion components", "x": xs, "y": ys,
                         "text": names, "line": {"width": 6}, "marker": {"size": 5},
                         "hovertemplate": "%{text}: TVD %{y:,.0f} m<extra></extra>"})
    logs = [lg for lg in depths.get("logs") or [] if any(t is not None for t in lg["tvd_m"])][:LOG_TRACKS]
    tracks = {}
    for n, lg in enumerate(logs, 2):  # a track per depth log curve, beside the section, sharing its depth axis
        pts = [(v, t) for v, t in zip(lg["values"], lg["tvd_m"]) if t is not None and v is not None]
        data.append({"type": "scatter", "mode": "lines", "name": f"{lg['curve']} ({lg['unit']})", "xaxis": f"x{n}",
                     "x": [v for v, _ in pts], "y": [t for _, t in pts], "line": {"width": 1},
                     "hovertemplate": f"{lg['curve']} %{{x:,.1f}} {lg['unit']} at TVD %{{y:,.0f}} m<extra></extra>"})
        width = 0.4 / len(logs)
        start = 0.6 + (n - 2) * width
        tracks[f"xaxis{n}"] = {"domain": [round(start + 0.03, 3), round(start + width, 3)], "anchor": "y",
                               "title": {"text": f"{lg['curve']} ({lg['unit']})"},
                               **({"type": "log"} if lg["unit"].lower() in ("ohmm", "ohm.m") else {})}
    headline = (f"Drilled to {s['td_md_m']:,.0f} m along the hole, {s['td_tvd_m']:,.0f} m true vertical depth, "
                f"{s['displacement_m']:,.0f} m out from the wellhead." if s else "No directional survey is loaded for this well.")
    rows = [[str(c["description"] or c["label"]), _number(c["top_md_m"]), _number(c["base_md_m"]), _number(c["top_tvd_m"]),
             _number(c["base_tvd_m"]), _number(c["size_mm"])] for c in comps]
    return Figure(
        kind="well-trajectory", title=f"{label}: trajectory and completion", data=data,
        layout=layout("", height=HEIGHT + 60, xaxis={"title": {"text": "Distance out from the wellhead (m)"},
                                                     **({"domain": [0, 0.56]} if tracks else {})}, **tracks,
                      yaxis={"title": {"text": "True vertical depth (m)"}, "autorange": "reversed"},
                      margin={"t": 24, "r": 16, "b": 60, "l": 64}, legend={"orientation": "h", "x": 0, "y": 1.12, "font": {"size": 11}}),
        summary={
            "context": f"Directional survey, {len(traj)} stations, by minimum curvature · {notice}",
            "headline": headline,
            "stats": [{"label": "Measured depth", "value": _number(s["td_md_m"]), "unit": "m"},
                      {"label": "True vertical depth", "value": _number(s["td_tvd_m"]), "unit": "m"},
                      {"label": "Maximum inclination", "value": _number(s["max_inclination_deg"]), "unit": "°"},
                      {"label": "Worst dogleg", "value": _number(s["max_dls_deg_30m"]), "unit": "°/30 m"}] if s else [],
            "notes": (["Depths are below the first survey station (the tie-in), not below sea level."] if traj else [])
            + [f"{lg['curve']} ({lg['description'] or lg['unit']}) from {lg['file']}, {len(lg['md_m'])} samples by "
               "measured depth, placed at true vertical depth by the survey." for lg in logs],
            "warnings": [f"{c['description'] or c['label']} is below the last survey station: its true vertical depth "
                         "is not known." for c in comps if c["base_md_m"] is not None and c["base_tvd_m"] is None],
            "tables": [{"title": f"Completion components · {len(comps)}", "note": "Outermost first at each top depth.",
                        "columns": ["Component", "Top MD (m)", "Base MD (m)", "Top TVD (m)", "Base TVD (m)", "Size (mm)"],
                        "rows": rows}] if comps else [],
            "source": "the drilling system's directional survey and completion components",
        },
    )


def _well(conn, facility: str, notice: str, well: str | None = None, **_) -> Figure:
    well = well or facility  # without a well, what was asked for: a facility has no survey, and the figure says so
    label = conn.execute("select label from entity_label where fuzer_id = %s", (well,)).fetchone()
    return well_trajectory(positions.well(conn, well), label["label"] if label else well, notice)


def _production(conn, facility: str, notice: str, **_) -> Figure:
    return production_efficiency(pe.production_efficiency(conn, facility), notice)


def _capacity(conn, facility: str, notice: str, **_) -> Figure:
    return flow_capacity(fc.flow_capacity(conn, facility), notice)


def _turnaround(conn, facility: str, notice: str, revision: str | None = None, **_) -> Figure:
    scope = turnaround_scope(conn, revision)
    return turnaround_progress(scope.get("schedule"), scope.get("revision"), notice)


FIGURES = {"production-efficiency": _production, "flow-capacity": _capacity, "turnaround-progress": _turnaround,
           "well-trajectory": _well}


def live(conn, pack, kind: str, facility: str, **opts) -> dict:
    """A live figure; opts carry what a figure needs beyond the facility (a turnaround's revision)."""
    if kind not in FIGURES:
        raise ChartError(f"no live figure {kind!r}")
    fig = FIGURES[kind](conn, facility, pack.data_notice or LIVE_NOTICE, **opts)
    check(fig)
    return {"kind": fig.kind, "title": fig.title, "spec": fig.spec, "summary": fig.summary, "sources": fig.sources}
