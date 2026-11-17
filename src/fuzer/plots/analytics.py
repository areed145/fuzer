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
    return UNKNOWN if value is None else f"{_number(value)} bbl/d"


def _compact(value: float) -> str:
    return f"{value / 1000:.1f}k".replace(".0k", "k") if value >= 1000 else f"{value:.0f}"


def _words(value: str) -> str:
    return value.replace("_", " ")


def _constraint(c: dict) -> str:
    """A constraint by name, with its category in words where the name doesn't already say it (#1072):
    "Export (export)" is just "Export", "Reported well potential [wells]" is "Reported well potential"."""
    label, category = str(c["label"]), str(c.get("category") or "").replace("_", " ")
    stem = category.rstrip("s").lower()
    return label if not stem or stem in label.lower() else f"{label} ({category})"


def constraint_label(point: dict, constraints: list[dict]) -> str:
    """What bound a day, or why that is not known. Ties name every binding constraint."""
    ids = sorted({*point["binding_constraint_ids"], *([point["active_constraint_id"]] if point["active_constraint_id"] else [])})
    known = {c["id"]: c for c in constraints}
    if point["dynamic_max_bbld"] is None or point["capacity_basis"] == "missing" or not set(ids) <= set(known):
        return UNKNOWN
    if point["capacity_basis"] == "reported_potential":
        return "Reported potential · physical constraint unknown"
    if not ids:
        return UNKNOWN
    labels = " + ".join(_constraint(known[id]) for id in ids)
    return f"{labels}, tied" if len(ids) > 1 else labels


def production_efficiency(out: dict, notice: str) -> Figure:
    """Allocated oil against the daily dynamic maximum. Unknown days stay gaps, never zero."""
    points, constraints, summary = out["points"], out["constraints"], out["summary"]
    days = [p["date"] for p in points]

    def hover(p: dict, field: str, label: str) -> str:
        lines = [f"{label}: {_rate(p[field])}"]
        if field == "allocated_bbld":
            lines.append(f"Efficiency: {_number(p['efficiency_pct'])} %")
        if field == "dynamic_max_bbld":
            lines += [f"Binding: {constraint_label(p, constraints)}", f"Capacity basis: {_words(p['capacity_basis'])}",
                      f"Flags: {', '.join(p['flags']) or 'none'}"]
        return "<br>".join(escape(line) for line in lines)

    # At a near-100% day the two rate lines overlap on a zero-based axis (#307), so the maximum is dashed (it shows
    # through allocated in either theme) and the gap gets its own fitted axis on the right, where its changes show.
    data = []
    for field, label, extra in [("dynamic_max_bbld", "Dynamic maximum", {"line": {"width": 2, "dash": "dash"}}),
                                ("allocated_bbld", "Allocated production", {}),
                                ("gap_bbld", "Unrealized gap", {"yaxis": "y2", "mode": "lines", "line": {"width": 1.5, "dash": "dot"}})]:
        data.append({"type": "scatter", "mode": "lines+markers", "name": f"{label} (bbl/d)", "x": days,
                     "y": [p[field] for p in points], "connectgaps": False, "marker": {"size": 8}, "line": {"width": 2},
                     "text": [hover(p, field, label) for p in points], "hovertemplate": "%{text}<extra></extra>", **extra})
    efficiency = summary["efficiency_pct"]
    gaps = [p for p in points if p["gap_bbld"] is not None]
    lost = sum(p["gap_bbld"] for p in gaps) if gaps else None
    by_cause: dict[str, float] = {}
    for p in gaps:
        by_cause[constraint_label(p, constraints)] = by_cause.get(constraint_label(p, constraints), 0) + p["gap_bbld"]
    cause = max(by_cause, key=by_cause.get) if lost else None
    rows = [[p["date"], _number(p["dynamic_max_bbld"]), _number(p["allocated_bbld"]), _number(p["gap_bbld"]),
             _number(p["efficiency_pct"]), constraint_label(p, constraints),
             _words(p["capacity_basis"]) + (f" · {', '.join(p['flags'])}" if p["flags"] else "")] for p in points]
    return Figure(
        kind="production-efficiency", title="Production efficiency", data=data if summary["paired_days"] else [],
        layout=layout("", height=HEIGHT, hovermode="x unified", margin={"t": 12, "r": 64, "b": 60, "l": 64},
                      xaxis={"title": {"text": "Production day (UTC)"}, "type": "date", "tickformat": "%d %b"},
                      yaxis={"title": {"text": "Oil rate (bbl/d)"}, "rangemode": "tozero"},
                      # its own round ticks from zero, not ticks synced to the rate axis's gridlines (-17, 65, 147…)
                      yaxis2={"title": {"text": "Gap (bbl/d)"}, "overlaying": "y", "side": "right", "showgrid": False,
                              "tickmode": "auto", "nticks": 5, "rangemode": "tozero"},
                      legend={"orientation": "h", "y": -0.28, "x": 0, "font": {"size": 11}},
                      uirevision=f"{out['facility']['id']}:{out['end']}"),
        summary={
            "context": f"{out['facility']['label']} · {days[0]} to {out['end']} inclusive · {notice}",
            "headline": _efficiency_sentence(efficiency, lost, cause, summary["paired_days"], len(points)),
            # three tiles, not six (#1074): the sentence above says the rest, and the totals sit in the notes and table
            "stats": [
                # lead with what was lost and why (#307): at ~100% efficiency the totals alone say little
                {"label": "Short of the maximum", "value": _number(lost), "unit": "bbl"},
                {"label": "Weighted efficiency", "value": "Unknown" if efficiency is None else f"{_number(efficiency)}%"},
                *([{"label": "Mostly held back by", "value": cause}] if cause else []),
            ] if summary["paired_days"] else [],
            "notes": [f"Over the {summary['paired_days']} days with both values: {_number(summary['allocated_bbl'])} bbl allocated against "
                      f"a dynamic maximum of {_number(summary['dynamic_max_bbl'])} bbl.",
                      "Unknown values are gaps, never zero."],
            "warnings": out["warnings"],
            "tables": [{"title": "Daily data", "columns": [
                "Day", "Maximum (bbl/d)", "Allocated (bbl/d)", "Gap (bbl/d)", "Efficiency (%)", "Binding constraints",
                "Capacity basis / flags"], "rows": rows}],
            "source": out["source"],
        },
        sources=sorted({id for p in points for id in p["source_ids"]}),
    )


def _efficiency_sentence(efficiency: float | None, lost: float | None, cause: str | None, paired: int, days: int) -> str:
    """The figure in one sentence (#1074): how close to potential, how much short, and mostly why."""
    if not paired:
        return "No paired production and capacity values in this window."
    span = f"{paired} days" if paired == days else f"{paired} of {days} days"
    head = f"{_number(efficiency)}% of potential over {span}" if efficiency is not None else f"Efficiency unknown over {span}"
    if not lost:
        return f"{head}, with no unrealized gap."
    why = f", mostly from {cause}" if cause and cause != UNKNOWN else ""
    return f"{head}; {_number(lost)} bbl short{why}."


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
        # Name the next tightest stage, so how much headroom the rest of the system has shows (#1074).
        rest = sorted((st for st in out["stages"] if not st["binding"] and st["capacity_bbld"] is not None),
                      key=lambda st: st["capacity_bbld"])
        if rest and system["limit_bbld"]:
            near, limit = rest[0], system["limit_bbld"]
            more = (near["capacity_bbld"] - limit) / limit * 100
            return (f"Bottleneck: {binding} at {_rate(limit)}. Next is {near['label']} at {_rate(near['capacity_bbld'])}, "
                    f"{_number(round(more, 1))}% more.")
        return f"Bottleneck: {binding} at {_rate(system['limit_bbld'])}."
    if system["basis"] == "declared_limits":
        return (f"Lowest declared bound is {binding} at {_rate(system['limit_bbld'])}, but some stages have no "
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
    lines = [stage["label"], f"{'At least' if stage['basis'] == 'demonstrated' else 'Capacity'}: {_rate(stage['capacity_bbld'])}",
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
    notes.append(f"{_system_label(out['system'])}: {_rate(out['system']['limit_bbld'])} on {day}, the same maximum as "
                 f"the production chart. Allocated that day: {_rate(out['system']['allocated_bbld'])}.")
    if any(s["basis"] == "demonstrated" for s in stages) and peak["peak_bbld"] is not None:
        notes.append(f"≥ shows the highest allocated day from {peak['since']} to {day}: {_rate(peak['peak_bbld'])} on "
                     f"{peak['date']}. The facility delivered that through this network, so each stage allows at "
                     "least this much. It is not a rating.")
    if any(s["basis"] == "well_tests" for s in stages):
        count = tests["not_tested"]
        untested = f" {count} {'well has' if count == 1 else 'wells have'} no test since {tests['since']}." if count else ""
        notes.append(f"Wells show the sum of each well's latest test, {tests['first']} to {tests['last']}: "
                     f"{_rate(tests['total_bbld'])} across {tests['wells']} wells, unreconciled to allocation.{untested} "
                     "A test rate is not unconstrained well potential.")
    return notes


def flow_capacity(out: dict, notice: str) -> Figure:
    """Capacity per flow stage beside the system limit, on the latest report day."""
    stages, system = out["stages"], out["system"]
    labels = [escape(s["label"]) for s in stages]
    limit = system["limit_bbld"]
    data = []
    for key, name, prefix, pattern in SERIES:
        if key == "limit":
            # The system limit is one dashed line across the stages, labelled once at its end (#1074), not a bar
            # repeating the same number beside every stage.
            y = [limit] * len(stages)
            data.append({"type": "scatter", "mode": "lines+text", "name": _system_label(system), "x": labels, "y": y,
                         "line": {"width": 2, "dash": "dash"}, "showlegend": limit is not None,
                         "text": ["" if limit is None or i < len(stages) - 1 else _compact(limit) for i in range(len(stages))],
                         "textposition": "top left", "cliponaxis": False,
                         "hovertext": [f"{escape(_system_label(system))}: {_rate(limit)}"] * len(stages),
                         "hovertemplate": "%{hovertext}<extra></extra>"})
            continue
        y = [s["capacity_bbld"] if _series(s) == key else None for s in stages]
        text = ["" if v is None else f"{'◆ ' if s['binding'] else ''}{prefix}{_compact(v)}" for v, s in zip(y, stages)]
        hover = [_stage_hover(s, out) for s in stages]
        data.append({"type": "bar", "name": name, "x": labels, "y": y, "offsetgroup": "stage",
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
                      yaxis={"title": {"text": "Capacity (bbl/d oil equivalent)"}, "rangemode": "tozero", "fixedrange": True},
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
    out = [round((t["north_ft"] ** 2 + t["east_ft"] ** 2) ** 0.5, 1) for t in traj]
    data = []
    if traj:
        data.append({"type": "scatter", "mode": "lines", "name": "Hole (survey)", "x": out, "y": [t["tvd_ft"] for t in traj],
                     "customdata": [t["md_ft"] for t in traj], "line": {"width": 2},
                     "hovertemplate": "MD %{customdata:,.0f} ft: TVD %{y:,.0f} ft, %{x:,.0f} ft out<extra></extra>"})
        mds = [t["md_ft"] for t in traj]

        def along(md: float) -> float | None:
            for a, b, xa, xb in zip(mds, mds[1:], out, out[1:]):
                if a <= md <= b:
                    return round(xa + (md - a) / (b - a) * (xb - xa), 1) if b > a else xa
            return None

        xs, ys, names = [], [], []
        for c in comps:
            if c["top_md_ft"] is None or c["base_md_ft"] is None or c["top_tvd_ft"] is None or c["base_tvd_ft"] is None:
                continue
            xa, xb = along(c["top_md_ft"]), along(c["base_md_ft"])
            if xa is None or xb is None:
                continue
            xs += [xa, xb, None]
            ys += [c["top_tvd_ft"], c["base_tvd_ft"], None]
            names += [c["description"]] * 2 + [None]
        if xs:
            data.append({"type": "scatter", "mode": "lines+markers", "name": "Completion components", "x": xs, "y": ys,
                         "text": names, "line": {"width": 6}, "marker": {"size": 5},
                         "hovertemplate": "%{text}: TVD %{y:,.0f} ft<extra></extra>"})
    logs = [lg for lg in depths.get("logs") or [] if any(t is not None for t in lg["tvd_ft"])][:LOG_TRACKS]
    tracks = {}
    for n, lg in enumerate(logs, 2):  # a track per depth log curve, beside the section, sharing its depth axis
        pts = [(v, t) for v, t in zip(lg["values"], lg["tvd_ft"]) if t is not None and v is not None]
        data.append({"type": "scatter", "mode": "lines", "name": f"{lg['curve']} ({lg['unit']})", "xaxis": f"x{n}",
                     "x": [v for v, _ in pts], "y": [t for _, t in pts], "line": {"width": 1},
                     "hovertemplate": f"{lg['curve']} %{{x:,.1f}} {lg['unit']} at TVD %{{y:,.0f}} ft<extra></extra>"})
        width = 0.4 / len(logs)
        start = 0.6 + (n - 2) * width
        tracks[f"xaxis{n}"] = {"domain": [round(start + 0.03, 3), round(start + width, 3)], "anchor": "y",
                               "title": {"text": f"{lg['curve']} ({lg['unit']})"},
                               **({"type": "log"} if lg["unit"].lower() in ("ohmm", "ohm.m") else {})}
    headline = (f"Drilled to {s['td_md_ft']:,.0f} ft along the hole, {s['td_tvd_ft']:,.0f} ft true vertical depth, "
                f"{s['displacement_ft']:,.0f} ft out from the wellhead." if s else "No directional survey is loaded for this well.")
    rows = [[str(c["description"] or c["label"]), _number(c["top_md_ft"]), _number(c["base_md_ft"]), _number(c["top_tvd_ft"]),
             _number(c["base_tvd_ft"]), _number(c["size_in"])] for c in comps]
    return Figure(
        kind="well-trajectory", title=f"{label}: trajectory and completion", data=data,
        layout=layout("", height=HEIGHT + 60, xaxis={"title": {"text": "Distance out from the wellhead (ft)"},
                                                     **({"domain": [0, 0.56]} if tracks else {})}, **tracks,
                      yaxis={"title": {"text": "True vertical depth (ft)"}, "autorange": "reversed"},
                      margin={"t": 24, "r": 16, "b": 60, "l": 64}, legend={"orientation": "h", "x": 0, "y": 1.12, "font": {"size": 11}}),
        summary={
            "context": f"Directional survey, {len(traj)} stations, by minimum curvature · {notice}",
            "headline": headline,
            "stats": [{"label": "Measured depth", "value": _number(s["td_md_ft"]), "unit": "ft"},
                      {"label": "True vertical depth", "value": _number(s["td_tvd_ft"]), "unit": "ft"},
                      {"label": "Maximum inclination", "value": _number(s["max_inclination_deg"]), "unit": "°"},
                      {"label": "Worst dogleg", "value": _number(s["max_dls_deg_100ft"]), "unit": "°/100 ft"}] if s else [],
            "notes": (["Depths are below the first survey station (the tie-in), not below sea level."] if traj else [])
            + [f"{lg['curve']} ({lg['description'] or lg['unit']}) from {lg['file']}, {len(lg['md_ft'])} samples by "
               "measured depth, placed at true vertical depth by the survey." for lg in logs],
            "warnings": [f"{c['description'] or c['label']} is below the last survey station: its true vertical depth "
                         "is not known." for c in comps if c["base_md_ft"] is not None and c["base_tvd_ft"] is None],
            "tables": [{"title": f"Completion components · {len(comps)}", "note": "Outermost first at each top depth.",
                        "columns": ["Component", "Top MD (ft)", "Base MD (ft)", "Top TVD (ft)", "Base TVD (ft)", "Size (in)"],
                        "rows": rows}] if comps else [],
            "source": "the drilling system's directional survey and completion components",
        },
    )


def _well(conn, facility: str, notice: str, well: str | None = None, **_) -> Figure:
    well = well or facility  # without a well, what was asked for: a facility has no survey, and the figure says so
    label = conn.execute("select label from entity_label where fuzer_id = %s", (well,)).fetchone()
    return well_trajectory(positions.well(conn, well), label["label"] if label else well, notice)


def well_section(conn, well: str) -> Figure:
    """The well-trajectory figure for one well (#363), for the Wells & Subsurface agent's plot tool."""
    from ..core.pack import current_pack

    return _well(conn, well, current_pack(conn).notice, well=well)


def _unreported(conn, facility: str) -> str | None:
    """A live facility's label when it has no daily production report, else None."""
    if pe.latest_report_date(conn, facility) is not None:
        return None
    row = conn.execute("select coalesce(l.label, e.fuzer_id) as label from entity e left join entity_label l using (fuzer_id) "
                       "where e.fuzer_id = %s and e.kind = 'facility' and e.retired_at is null and e.merged_into is null",
                       (facility,)).fetchone()
    return row["label"] if row else None


def _no_reports(kind: str, title: str, label: str, notice: str) -> Figure:
    """A facility with no daily production report (#1074): a figure that says so, with nothing to plot, rather than
    an error a page shows with a Try again button that can never help."""
    return Figure(kind=kind, title=title, data=[], layout=layout("", height=HEIGHT),
                  summary={"context": f"{label} · {notice}", "headline": f"No daily production reports are loaded for {label}.",
                           "stats": [], "notes": [], "warnings": [], "tables": [], "source": "daily production reports (EC DAY001)"})


def _production(conn, facility: str, notice: str, **_) -> Figure:
    if label := _unreported(conn, facility):
        return _no_reports("production-efficiency", "Production efficiency", label, notice)
    return production_efficiency(pe.production_efficiency(conn, facility), notice)


def _capacity(conn, facility: str, notice: str, **_) -> Figure:
    if label := _unreported(conn, facility):
        return _no_reports("flow-capacity", "System capacity and bottleneck", label, notice)
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
