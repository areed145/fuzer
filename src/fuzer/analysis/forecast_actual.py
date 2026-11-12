"""Production forecast against actual, per well and across the field (P7.3b, #336): what each well was expected to make
from its own decline, against what it made, so a new loss stands apart from natural decline. The deferment Pareto
can't do this: a loss nobody recorded as downtime shows only as a gap to the forecast.

For each producing well, a decline is fitted (calcs.decline_fit) through its tests before the window opens, leaving out
a dip it recovered from (a gas lift valve that failed open and was fixed), and
projected through the window with its 90 % range (forecast.arps_interval). Each test in the window is set against it:

    below_forecast     the latest test fell under the range, and by at least 5 % of the forecast: a lead for review
                       (a new loss, not decline), with the test it started from
    above_forecast     the latest test rose over it (a workover, a choke opened, a test to check)
    on_forecast        within the range: natural decline explains it
    no_test_in_window  nothing to compare yet
    too_little_data    too few tests before the window to fit; the well is listed, never fitted (#334)

The actual is the well test rate, since a per-well allocation isn't loaded; downtime on the well in the window is
marked beside it. The field line sums the latest forecast and actual over the wells that have both. Gaps are leads
for an engineer, and the reservoir cause stays unknown: this never names one.
"""

import math
from datetime import date, datetime, timedelta

from ..graph import operations as ops
from ..graph.queries import _DOWNTIME
from . import calcs, forecast
from .calcs import YEAR

MIN_TESTS = 6  # tests before the window a fit rests on
MIN_SPAN_DAYS = 120  # and the days they span
DIP = 0.25  # a test this far under the first fit (in log rate, about 22 %) is a dip, if the well came back after it
MIN_GAP = 0.05  # a test outside the range counts only when it is also this share of the forecast away


def _wells(conn, ref: str | None) -> list[dict]:
    if ref:
        return conn.execute("select e.fuzer_id as id, coalesce(l.label, e.fuzer_id) as label from entity e left join "
                            "entity_label l using (fuzer_id) where e.fuzer_id = %s", (ref,)).fetchall()
    return conn.execute(
        "select e.fuzer_id as id, coalesce(l.label, e.fuzer_id) as label from entity e left join entity_label l "
        "using (fuzer_id) where e.kind = 'well' and e.retired_at is null and e.merged_into is null and exists "
        "(select 1 from measurement m where m.fuzer_id = e.fuzer_id and m.method = 'well test' "
        "and m.quantity = 'oil_rate') order by 2").fetchall()


def _downtime(conn, well: str, since: str, until: str) -> list[dict]:
    rows = conn.execute(_DOWNTIME + "select id, f from dd order by f->>'start'",
                        {"id": well, "since": since, "until": until}).fetchall()
    return [{"id": r["id"], "start": r["f"].get("start"), "end": r["f"].get("end"), "reason": r["f"].get("reason"),
             "lost_oil_sm3": calcs._r(ops._num(r["f"].get("lost_oil_sm3")), 1)} for r in rows]


def well(conn, wid: str, label: str, since: str, until: str) -> dict:
    """One well's forecast against its tests in [since, until)."""
    start, end = datetime.fromisoformat(since), datetime.fromisoformat(until)
    try:
        tests = [t for t in calcs.well_tests(conn, wid) if t.get("oil_rate", 0) > 0]
    except calcs.CalcError:
        tests = []
    before = [t for t in tests if t["at"] < start]
    inside = [t for t in tests if start <= t["at"] < end]
    base = {"id": wid, "well": label, "tests_before": len(before), "tests_in_window": len(inside),
            "downtime": _downtime(conn, wid, since, until)}
    enough = forecast.sufficiency([t["at"] for t in before], MIN_TESTS, MIN_SPAN_DAYS)
    if not enough["quotable"]:
        return base | {"verdict": "too_little_data", "sufficiency": enough, "reason": "; ".join(enough["reasons"])}
    d = calcs.decline_fit(conn, wid, until=since)
    fit, seg, t0 = d["fit"], d["seg"], d["t0"]
    ts, qs = [(t["at"] - t0).days / YEAR for t in seg], [t["oil_rate"] for t in seg]
    # a dip that recovered (a valve failed open, then fixed) isn't the well's decline: leave its tests out and refit
    dipped = [i for i, (x, q) in enumerate(zip(ts, qs)) if math.log(q / calcs.rate(fit, x)) < -DIP]
    if dipped and dipped[-1] < len(qs) - 1 and len(qs) - len(dipped) >= MIN_TESTS:
        ts, qs = [x for i, x in enumerate(ts) if i not in dipped], [q for i, q in enumerate(qs) if i not in dipped]
        fit = calcs.arps(ts, qs)
    points = []
    for t in inside:
        b = forecast.arps_interval(fit, ts, qs, (t["at"] - t0).days / YEAR)
        low, high = b["range"] or (None, None)
        q = t["oil_rate"]
        material = abs(q - b["value"]) >= MIN_GAP * b["value"]
        points.append({"at": t["at"].isoformat()[:10], "record": t["record"], "actual_sm3d": calcs._r(q, 1),
                       "forecast_sm3d": calcs._r(b["value"], 1), "low_sm3d": calcs._r(low, 1),
                       "high_sm3d": calcs._r(high, 1), "gap_sm3d": calcs._r(q - b["value"], 1),
                       "outside": None if not material else "below" if low is not None and q < low else
                                  "above" if high is not None and q > high else None})
    fitted = {"model": fit["model"], "tests": len(ts), "left_out": [seg[i]["at"].isoformat()[:10] for i in dipped]
              if len(ts) < len(seg) else [], "from": t0.isoformat()[:10],
              "to": seg[-1]["at"].isoformat()[:10], "effective_decline_pct_per_year":
                  calcs._r((1 - calcs.rate(fit, 1) / calcs.rate(fit, 0)) * 100, 1), "level": forecast.LEVEL}
    if not points:
        return base | {"verdict": "no_test_in_window", "fit": fitted, "sufficiency": enough, "points": []}
    last = points[-1]
    verdict = {"below": "below_forecast", "above": "above_forecast"}.get(last["outside"] or "", "on_forecast")
    first_out = None  # the start of the latest unbroken run of tests under the range
    for p in reversed(points):
        if p["outside"] != "below":
            break
        first_out = p["at"]
    return base | {"verdict": verdict, "fit": fitted, "sufficiency": enough, "points": points,
                   "latest": last, "gap_sm3d": last["gap_sm3d"], "below_since": first_out,
                   "tests_below": sum(1 for p in points if p["outside"] == "below")}


def forecast_vs_actual(conn, ref: str | None = None, since: str | None = None, until: str | None = None,
                       days: int = 365) -> dict:
    """Every producing well's forecast against its tests in the window (default the `days` to the latest daily
    report), ranked by the gap, with the wells too thin to fit listed apart and a field line."""
    until = until or (date.fromisoformat(ops.as_of(conn)) + timedelta(days=1)).isoformat()
    since = since or (date.fromisoformat(until) - timedelta(days=days)).isoformat()
    rows = [well(conn, w["id"], w["label"], since, until) for w in _wells(conn, ref)]
    fitted = [r for r in rows if r["verdict"] not in ("too_little_data",)]
    compared = [r for r in fitted if r.get("latest")]
    exp_ = sum(r["latest"]["forecast_sm3d"] or 0 for r in compared)
    act = sum(r["latest"]["actual_sm3d"] or 0 for r in compared)
    order = {"below_forecast": 0, "above_forecast": 1, "on_forecast": 2, "no_test_in_window": 3}
    compared_rank = sorted(fitted, key=lambda r: (order[r["verdict"]], r.get("gap_sm3d") or 0, r["well"]))
    return {
        "since": since, "until": until, "source": "well tests (the actual: no per-well allocation is loaded), "
        "downtime records marked beside them", "unit": "Sm3/d oil", "level": forecast.LEVEL,
        "wells": compared_rank, "not_fitted": [{k: r[k] for k in ("id", "well", "tests_before", "reason")}
                                               for r in rows if r["verdict"] == "too_little_data"],
        "field": {"wells_compared": len(compared), "forecast_sm3d": round(exp_, 1), "actual_sm3d": round(act, 1),
                  "gap_sm3d": round(act - exp_, 1),
                  "below_forecast": [r["well"] for r in compared if r["verdict"] == "below_forecast"],
                  "above_forecast": [r["well"] for r in compared if r["verdict"] == "above_forecast"]},
        "note": "Forecasts are each well's own decline, an estimate and never a plan. A gap is a lead for review; "
                "the reservoir cause stays unknown.",
    }
