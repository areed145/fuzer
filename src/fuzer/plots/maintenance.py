"""Maintenance and inspection charts: wall thickness per CML (or the fastest wall loss across the
asset), reliability (Weibull or Crow-AMSAA) from failure notifications, and bad actors."""

import math
from datetime import date, datetime

from . import ChartError, Figure, layout, rnd


def wall_thickness(conn, fid: str | None = None, kind: str | None = None, min_required_mm: float | None = None,
                   limit: int = 20) -> Figure:
    """One item's CMLs over time with fitted loss lines (and retirement dates when the minimum required
    thickness is given); without an item, the items losing wall fastest across the asset."""
    from .. import measurements as m

    if fid is None:
        return _wall_ranking(conn, kind, limit)
    series = m.for_entity(conn, fid, "wall_thickness")["series"]
    if not series:
        raise ChartError("no wall thickness readings on that item")
    worst = min(series, key=lambda s: s["per_year"] if s["per_year"] is not None else 0)
    label = series[0]["of"]["label"]
    data, cmls = [], []
    for s in series:
        vals = s["values"]
        ts = [datetime.fromisoformat(v["at"]) for v in vals]
        hot = s is worst
        data.append({"type": "scatter", "mode": "lines+markers", "name": s["location"] or "?",
                     "x": [v["at"][:10] for v in vals], "y": [v["value"] for v in vals],
                     "text": [v["reported"] and f"reported {v['reported']}" or (v["method"] or "") for v in vals],
                     "line": {"width": 3 if hot else 1.5}, "meta": {"entity": s["of"]["id"]},
                     "hovertemplate": "%{y:.2f} mm<br>%{x}<br>%{text}<extra>%{fullData.name}</extra>"})
        retire = None
        if min_required_mm and s["per_year"] and s["per_year"] < 0:
            years = (s["latest"]["value"] - min_required_mm) / -s["per_year"]
            retire = ts[-1].year + years
            end = ts[-1].replace(year=min(2200, int(retire)))
            data.append({"type": "scatter", "mode": "lines", "name": f"{s['location']} projected", "showlegend": False,
                         "x": [ts[-1].isoformat()[:10], end.isoformat()[:10]], "y": [s["latest"]["value"], min_required_mm],
                         "line": {"dash": "dot", "width": 1}})
        cmls.append({"cml": s["location"], "readings": s["count"], "latest_mm": rnd(s["latest"]["value"], 2),
                     "latest_at": s["latest"]["at"][:10], "loss_mm_per_year": rnd(-(s["per_year"] or 0), 3),
                     **({"retirement_year": rnd(retire, 1)} if retire else {})})
    lay = layout(f"Wall thickness at the CMLs on {label}", None, "wall thickness (mm)")
    if min_required_mm:
        lay["shapes"] = [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": "y", "y0": min_required_mm,
                          "y1": min_required_mm, "line": {"color": "rgba(210,60,50,0.8)", "dash": "dash"}}]
    cmls.sort(key=lambda c: -c["loss_mm_per_year"])
    return Figure("wall_thickness", lay["title"]["text"], data, lay, {
        "item": label, "cmls": cmls, "fastest": cmls[0]["cml"],
        "min_required_mm": min_required_mm,
        "note": None if min_required_mm else "no minimum required thickness given, so no retirement dates"}, [fid])


def _wall_ranking(conn, kind: str | None, limit: int) -> Figure:
    rows = conn.execute(
        """
        with cml as (
            select fuzer_id, location, count(*) as n,
                   regr_slope(value, extract(epoch from at) / 31557600) as per_year
            from measurement where quantity = 'wall_thickness' group by fuzer_id, location having count(*) >= 2
        )
        select c.fuzer_id, e.kind, l.label, g.value #>> '{}' as description,
               min(c.per_year) as worst, avg(c.per_year) as mean, count(*) as cmls
        from cml c join entity e using (fuzer_id) left join entity_label l using (fuzer_id)
        left join golden g on g.fuzer_id = c.fuzer_id and g.field = 'description'
        where %(kind)s::text is null or e.kind = %(kind)s
        group by c.fuzer_id, e.kind, l.label, g.value order by worst limit %(limit)s
        """, {"kind": kind, "limit": limit}).fetchall()
    if not rows:
        raise ChartError("no wall thickness readings")
    data = [{"type": "bar", "orientation": "h", "name": "fastest CML", "y": [r["label"] for r in rows],
             "x": [round(-r["worst"], 3) for r in rows], "customdata": [r["fuzer_id"] for r in rows],
             "text": [r["description"] or "" for r in rows], "hovertemplate": "%{y}: %{x:.3f} mm/year<br>%{text}<extra></extra>"},
            {"type": "bar", "orientation": "h", "name": "mean of its CMLs", "y": [r["label"] for r in rows],
             "x": [round(-r["mean"], 3) for r in rows], "customdata": [r["fuzer_id"] for r in rows]}]
    lay = layout("Fastest wall loss" + (f" ({kind}s)" if kind else ""), "wall loss (mm/year)", None, barmode="group")
    lay["yaxis"] = {"autorange": "reversed"}
    lay["height"] = 200 + 26 * len(rows)
    rates = sorted(-r["worst"] for r in rows)
    return Figure("wall_ranking", lay["title"]["text"], data, lay, {
        "items": [{"label": r["label"], "kind": r["kind"], "description": r["description"], "cmls": r["cmls"],
                   "fastest_loss_mm_per_year": rnd(-r["worst"], 3), "mean_loss_mm_per_year": rnd(-r["mean"], 3)}
                  for r in rows[:10]],
        "median_of_shown_mm_per_year": rnd(rates[len(rates) // 2], 3)}, [r["fuzer_id"] for r in rows])


def _failures(conn, fid: str) -> tuple[str, dict[str, list[date]]]:
    """Failure dates per unit for a tag / equipment, or for every unit in an equipment class."""
    e = conn.execute("select e.kind, l.label from entity e left join entity_label l using (fuzer_id) "
                     "where fuzer_id = %s", (fid,)).fetchone()
    if e is None:
        raise ChartError("unknown entity")
    units = [fid]
    if e["kind"] == "equipment_class":
        units = [r["src"] for r in conn.execute(
            "select distinct src from edge where rel = 'classified_as' and dst = %s", (fid,))]
    rows = conn.execute(
        """
        select c.dst as unit, g.value #>> '{}' as created from edge c
        join entity n on n.fuzer_id = c.src and n.kind = 'notification'
        join golden g on g.fuzer_id = c.src and g.field = 'created'
        where c.rel = 'concerns' and c.dst = any(%s)
        """, (units,)).fetchall()
    out: dict[str, list[date]] = {}
    for r in rows:
        out.setdefault(r["unit"], []).append(date.fromisoformat(r["created"][:10]))
    return e["label"] or fid, {u: sorted(d) for u, d in out.items()}


def reliability(conn, fid: str, method: str = "weibull") -> Figure:
    """Weibull probability plot of times between failures (median ranks; beta < 1 early failures,
    about 1 random, > 1 wear-out), or Crow-AMSAA cumulative failures on log-log (beta > 1: failures
    coming faster)."""
    label, per = _failures(conn, fid)
    if method == "crow_amsaa":
        dates = sorted(d for ds in per.values() for d in ds)
        if len(dates) < 4:
            raise ChartError("fewer than four failures")
        start = dates[0]
        ts = [max(1, (d - start).days) for d in dates]
        n = list(range(1, len(ts) + 1))
        pts = [(math.log(t), math.log(k)) for t, k in zip(ts, n) if t > 1]
        mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        beta = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x, _ in pts)
        lam = math.exp(my - beta * mx)
        data = [{"type": "scatter", "mode": "markers", "name": "failures", "x": ts, "y": n,
                 "text": [d.isoformat() for d in dates], "hovertemplate": "%{y} failures by day %{x}<br>%{text}<extra></extra>"},
                {"type": "scatter", "mode": "lines", "name": f"fit N = {lam:.3g} t^{beta:.2f}", "line": {"dash": "dash"},
                 "x": [ts[0], ts[-1]], "y": [lam * ts[0] ** beta, lam * ts[-1] ** beta]}]
        lay = layout(f"Crow-AMSAA: cumulative failures on {label}", f"days since {start}", "cumulative failures")
        lay["xaxis"]["type"] = lay["yaxis"]["type"] = "log"
        return Figure("crow_amsaa", lay["title"]["text"], data, lay, {
            "scope": label, "units": len(per), "failures": len(dates), "first": dates[0].isoformat(),
            "last": dates[-1].isoformat(), "beta": rnd(beta, 2), "lambda": rnd(lam, 4),
            "reading": "failures coming faster" if beta > 1.15 else "failures slowing" if beta < 0.85 else "steady rate"},
            [fid])
    tbf = sorted((b - a).days for ds in per.values() for a, b in zip(ds, ds[1:]) if (b - a).days > 0)
    if len(tbf) < 3:
        raise ChartError("fewer than three times between failures (need repeat failures on the same unit)")
    n = len(tbf)
    F = [(i + 1 - 0.3) / (n + 0.4) for i in range(n)]
    xs = [math.log(t) for t in tbf]
    ys = [math.log(-math.log(1 - f)) for f in F]
    mx, my = sum(xs) / n, sum(ys) / n
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    eta = math.exp(mx - my / beta)
    lo, hi = min(tbf) * 0.7, max(tbf) * 1.4
    ticks = [0.01, 0.05, 0.1, 0.2, 0.5, 0.632, 0.9, 0.99]
    data = [{"type": "scatter", "mode": "markers", "name": "times between failures", "x": tbf, "y": ys,
             "hovertemplate": "%{x} days<extra></extra>"},
            {"type": "scatter", "mode": "lines", "name": f"Weibull beta {beta:.2f}, eta {eta:.0f} d", "line": {"dash": "dash"},
             "x": [lo, hi], "y": [beta * (math.log(lo) - math.log(eta)), beta * (math.log(hi) - math.log(eta))]}]
    lay = layout(f"Weibull plot: times between failures on {label}", "days between failures", "unreliability F(t)")
    lay["xaxis"]["type"] = "log"
    lay["yaxis"] |= {"tickvals": [math.log(-math.log(1 - f)) for f in ticks], "ticktext": [f"{f * 100:g}%" for f in ticks]}
    return Figure("weibull", lay["title"]["text"], data, lay, {
        "scope": label, "units_with_repeats": sum(1 for d in per.values() if len(d) > 1), "times_between_failures": n,
        "beta": rnd(beta, 2), "eta_days": rnd(eta, 0), "median_days": rnd(eta * math.log(2) ** (1 / beta), 0),
        "reading": "early-life failures (installation, quality)" if beta < 0.85 else
        "random failures (time-based PM won't help)" if beta < 1.2 else "wear-out (a PM interval below eta helps)"},
        [fid])


def bad_actors(conn, since: str | None = None, until: str | None = None, limit: int = 25) -> Figure:
    """Bad actors as bubbles: failures against oil lost to the downtime they caused, bubble size =
    downtime events, colour = ISO 14224 class."""
    from .. import operations as ops

    out = ops.bad_actors(conn, since, until, limit)
    items = out["bad_actors"]
    if not items:
        raise ChartError("no failures in that window")
    data = []
    for cls in dict.fromkeys(i["class"] or "unclassified" for i in items):
        xs = [i for i in items if (i["class"] or "unclassified") == cls]
        data.append({"type": "scatter", "mode": "markers+text", "name": cls, "x": [i["failures"] for i in xs],
                     "y": [i["lost_oil_sm3"] for i in xs], "text": [i["tag"] for i in xs], "textposition": "top center",
                     "customdata": [i["id"] for i in xs],
                     "marker": {"size": [10 + 4 * min(10, i["downtime_events"]) for i in xs], "opacity": 0.7},
                     "hovertemplate": "%{text}: %{x} failures, %{y:.0f} Sm3 lost<extra>%{fullData.name}</extra>"})
    lay = layout(f"Bad actors {out['since']} to {out['until']}", "failures (corrective notifications)", "oil lost (Sm3)")
    return Figure("bad_actors", lay["title"]["text"], data, lay, {
        "since": out["since"], "until": out["until"],
        "top": [{k: i[k] for k in ("tag", "class", "failures", "mtbf_days", "lost_oil_sm3", "downtime_events",
                                    "repeat_failure_modes")} for i in items[:6]]}, [i["id"] for i in items])
