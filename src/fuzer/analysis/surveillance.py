"""Production surveillance checks (#515): is a well test believable, and where is the field's oil going.

- `test_check(well)`: the latest well test against the well's own inflow and its gas lift curve, before anyone acts
  on it. Inflow: the liquid rate per bar of wellhead drawdown (shut-in wellhead pressure less the flowing wellhead
  pressure the historian saw on the test day), against the same figure from the well's earlier tests. Lift: the
  oil rate the well's gas lift curve (fitted to its earlier tests) expects at the test's lift gas rate. Flags
  `test_suspect` when the test departs from the inflow by 25 % or more (the wellhead pressures didn't see the rate
  change: retest before acting) and `off_lift_curve` when the inflow agrees but the oil is 25 % or more below the
  lift curve (a lift problem: a valve, the gas supply).
- `deferment_attribution(scope)`: every oil well's shortfall against its potential (its latest test against the
  decline fitted through the tests before it),
  attributed to one cause per well: recorded deferment (EC downtime against the well, by reason), back-pressure or
  the choke (the well's constraint now), lift (its gas lift curve says more gas pays, or it is below its curve), a
  suspect test, or nothing on record. The causes add up across wells, so the IOC sees how much is unexplained.

Both use the well tests, the historian's wellhead pressure and the EC deferment records, nothing else.
"""

import math
from datetime import datetime, timedelta

import numpy as np

from .calcs import YEAR, CalcError, _label, _r, result

SUSPECT = 0.25  # fractional departure from the well's own inflow that makes a test suspect
OFF_CURVE = 0.25  # fractional shortfall below the gas lift curve at the test's lift gas
INFLOW_ONLY = 0.40  # departure from the inflow alone, when the choke can't be checked, that makes a test suspect
NOISE = 0.05  # a shortfall under this share of potential (or twice the tests' scatter) is within their noise
WATER_RISE = 8.0  # points of water cut on the test before that make the shortfall water
BACK_RISE = 1.0  # bar the header must sit above its level of the previous months to hold a well back


def _liquid(t: dict) -> float | None:
    oil, wc = t.get("oil_rate"), t.get("water_cut")
    if not oil or wc is None or wc >= 99:
        return None
    return oil / (1 - wc / 100)


def _whp_on(conn, pts: dict, day: datetime, src=None) -> float | None:
    from . import calcs

    v = calcs.read({"whp": pts["whp"]}, day, day + timedelta(days=1), 60, src)["whp"]
    return float(np.median(list(v.values()))) if v else None


def test_check(conn, well: str, until: str | None = None, history: int = 6, src=None) -> dict:
    """The latest test against the well's inflow (liquid per bar of wellhead drawdown) and its gas lift curve."""
    from . import calcs
    from .hydraulics import _shut_in_whp, well_points

    tests = [t for t in calcs.well_tests(conn, well, until) if _liquid(t)][-history:]
    if len(tests) < 4:
        raise CalcError("fewer than four oil well tests with a water cut on that well")
    pts = well_points(conn, well)
    end = tests[-1]["at"] + timedelta(days=1)
    psi = _shut_in_whp(conn, pts, end, days=730, src=src)
    if psi is None:
        raise CalcError("no shut-in wellhead pressure in the last two years, so no inflow to check against")
    rows = []
    for t in tests:
        whp = _whp_on(conn, pts, t["at"], src)
        if whp is None or psi - whp < 1:
            continue
        rows.append({"at": t["at"].date().isoformat(), "record": t["record"], "liquid": _liquid(t),
                     "oil": t["oil_rate"], "whp": whp, "pi": _liquid(t) / (psi - whp), "lift_gas": t.get("lift_gas_rate")})
    if len(rows) < 3 or rows[-1]["record"] != tests[-1]["record"]:
        raise CalcError("too few tests with a flowing wellhead pressure on the day to compare")
    last, before = rows[-1], rows[:-1]
    expected_pi = float(np.median([r["pi"] for r in before[-3:]]))
    inflow_dev = last["pi"] / expected_pi - 1
    expected_liquid = expected_pi * (psi - last["whp"])
    curve = lift_dev = None
    if last["lift_gas"]:
        try:
            g = calcs.gas_lift_fit(conn, well, until=last["at"])
            t_now = (tests[-1]["at"] - g["tests"][0]["at"]).days / YEAR
            f = g["fit"]
            curve = f["a"] + f["d"] * t_now + f["c"] * (1 - math.exp(-last["lift_gas"] / f["k"]))
            lift_dev = last["oil"] / curve - 1 if curve > 0 else None
        except CalcError:
            pass
    choke_dev = None
    try:  # outflow: the choke's effective Cv at the test's rate and the day's pressures, against the tests before
        op = calcs.choke_operation(conn, well, until=end.isoformat(), src=src)
        ratio = [r for r in op["ratio"] if not op["installs"] or r[0] >= op["installs"][-1]]  # this choke's own
        if len(ratio) >= 4 and ratio[-1][0][:10] == last["at"]:
            prev = sorted(r[3] for r in ratio[-4:-1])
            choke_dev = ratio[-1][3] / prev[1] - 1
    except CalcError:
        pass
    # A real change in the well moves its wellhead pressure through the choke; a test separator reading that the
    # choke and pressures don't support is the test, not the well.
    # Without the choke check the inflow alone is noisier (the test's own scatter moves it), so it needs more.
    suspect = abs(choke_dev) >= SUSPECT if choke_dev is not None else abs(inflow_dev) >= INFLOW_ONLY
    flag = ("test_suspect" if suspect else
            "off_lift_curve" if lift_dev is not None and lift_dev <= -OFF_CURVE else None)
    return result(_r(last["liquid"], 1), _r(expected_liquid, 1), flag,
                  f"liquid rate per bar of wellhead drawdown (shut-in wellhead pressure {psi:.1f} barg less the "
                  f"historian's median wellhead pressure on the test day) against the median of the previous "
                  f"{min(3, len(before))} tests; outflow: the rate through the choke at the test day's wellhead and header "
                  f"pressures (its effective Cv) against the previous three tests. Suspect when the choke check (else "
                  f"the inflow, at {INFLOW_ONLY:.0%}) is {SUSPECT:.0%} or more off either way"
                  + ("; oil against the well's gas lift curve fitted to its earlier tests at the test's lift gas, off "
                     f"the curve at {OFF_CURVE:.0%} or more below" if curve is not None else ""),
                  [well, last["record"], pts["whp"]["id"]], unit="Sm3/d liquid", at=last["at"],
                  inflow_deviation_pct=_r(inflow_dev * 100, 1),
                  choke_deviation_pct=_r(choke_dev * 100, 1) if choke_dev is not None else None, pi=_r(last["pi"], 2), expected_pi=_r(expected_pi, 2),
                  shut_in_whp_barg=_r(psi, 1), whp_barg=_r(last["whp"], 1), oil_sm3d=_r(last["oil"], 1),
                  lift_curve_oil_sm3d=_r(curve, 1), lift_deviation_pct=_r(lift_dev * 100, 1) if lift_dev is not None
                  else None, tests=[{k: (_r(v, 2) if isinstance(v, float) else v) for k, v in r.items()} for r in rows])


CAUSES = ("recorded deferment", "suspect test", "water", "flowline restriction", "back-pressure", "lift", "choke",
          "nothing on record")


def _back_rise(conn, pts: dict, at: str, src=None) -> float | None:
    """How far the well's header sits now above its median over the 90 days before the last week (bar)."""
    from . import calcs

    if not pts.get("back"):
        return None
    end = datetime.fromisoformat(at[:19])
    v = calcs.read({"back": pts["back"]}, end - timedelta(days=97), end, 360, src)["back"]
    cut = (end - timedelta(days=7)).isoformat()
    old, new = [x for t, x in v.items() if t < cut], [x for t, x in v.items() if t >= cut]
    return float(np.median(new) - np.median(old)) if old and new else None


def _deferred(conn, since: str, until: str) -> dict[str, dict]:
    """EC deferment against each well in the window: lost oil and its biggest reason."""
    from ..graph import operations

    rows = conn.execute(operations._DOWNTIME + """
        select c.dst as well, coalesce(dd.f->>'reason', 'Unknown') as reason,
               sum((dd.f->>'lost_oil_sm3')::numeric) as lost, sum((dd.f->>'duration_h')::numeric) as hours
        from dd join edge c on c.src = dd.id and c.rel = 'affects' join entity w on w.fuzer_id = c.dst and w.kind = 'well'
        group by 1, 2
    """, {"id": None, "since": since, "until": until}).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        o = out.setdefault(r["well"], {"lost_oil_sm3": 0.0, "hours": 0.0, "reasons": {}})
        o["lost_oil_sm3"] += float(r["lost"] or 0)
        o["hours"] += float(r["hours"] or 0)
        o["reasons"][r["reason"]] = round(float(r["lost"] or 0))
    return out


def _cause(conn, w: str, at: str, src=None) -> tuple[str, str | None]:
    """The first cause the evidence supports for one well's shortfall, in the order an engineer rules them out."""
    from . import calcs, hydraulics

    try:
        tc = test_check(conn, w, until=at, src=src)
        if tc["flag"] == "test_suspect":
            dev = tc["choke_deviation_pct"] if tc["choke_deviation_pct"] is not None else tc["inflow_deviation_pct"]
            return "suspect test", f"latest test {dev:+.0f} % off what its choke and pressures pass"
    except CalcError:
        pass
    tests = calcs.well_tests(conn, w, at)
    wc0, wc1 = tests[-2].get("water_cut"), tests[-1].get("water_cut")
    if wc0 is not None and wc1 is not None and wc1 - wc0 >= WATER_RISE:
        return "water", f"water cut {wc0:.0f} to {wc1:.0f} % between its last two tests"
    try:
        dd = hydraulics.dp_drift(conn, w, until=at, src=src)
        if dd["flag"] == "dp_rising":
            return "flowline restriction", f"wellhead to header drop rising since {dd['rising_since'][:10]}"
    except CalcError:
        pass
    try:
        wc = hydraulics.well_constraint(conn, w, at=at, src=src)
    except CalcError:
        wc = None
    if wc and wc["flag"] == "back_pressure":
        rise = _back_rise(conn, hydraulics.well_points(conn, w), at, src)
        if rise is not None and rise >= BACK_RISE:
            return "back-pressure", f"header {rise:+.1f} bar on the months before, choke subcritical"
    try:
        gl = calcs.gas_lift(conn, w, until=at)
        if gl["flag"] == "below_curve":
            return "lift", gl["flag"].replace("_", " ")
    except CalcError:
        pass
    if wc and wc["flag"] == "choke":
        return "choke", f"critical flow at {wc['choke_pct']} % open"
    return "nothing on record", None


def deferment_attribution(conn, fid: str | None = None, at: str | None = None, days: int = 30,
                          min_gap_sm3d: float = 2.0, src=None) -> dict:
    """Each oil well's shortfall (its latest test below the decline fitted through the tests before it) with one cause per well, summed
    by cause across the wells in scope (a jacket, area or facility; else the asset)."""
    from ..graph import operations
    from . import calcs, hydraulics

    at = at or (datetime.fromisoformat(operations.as_of(conn)) + timedelta(days=1)).isoformat()
    since = (datetime.fromisoformat(at[:19]) - timedelta(days=days)).date().isoformat()
    wells = hydraulics._wells(conn, fid) if fid else [r["fuzer_id"] for r in conn.execute(
        "select fuzer_id from entity where kind = 'well' and retired_at is null and merged_into is null order by 1")]
    deferred = _deferred(conn, since, at[:10])
    rows, unchecked = [], []
    for w in wells:
        try:
            tests = calcs.well_tests(conn, w, at)
            last = tests[-1]
            fit = calcs.decline_fit(conn, w, until=last["at"].isoformat())  # the tests before the latest
        except CalcError as e:
            unchecked.append({"well": _label(conn, w), "why": str(e)})
            continue
        potential = calcs.rate(fit["fit"], (last["at"] - fit["t0"]).days / YEAR)
        actual = last.get("oil_rate") or 0.0
        gap = (potential or 0) - actual
        d = deferred.get(w)
        cause, evidence = "nothing on record", None
        scatter = math.sqrt(fit["fit"]["sse"] / max(1, len(fit["seg"]) - 2))  # the tests' own scatter (log rate)
        counts = gap >= max(min_gap_sm3d, max(NOISE, 2 * scatter) * (potential or 0))
        if d and d["lost_oil_sm3"] > 0:
            cause = "recorded deferment"
            evidence = max(d["reasons"], key=d["reasons"].get)
        elif counts:
            cause, evidence = _cause(conn, w, at, src)
        rows.append({"well": _label(conn, w), "id": w, "potential_sm3d": _r(potential, 1), "latest_test_sm3d": _r(actual, 1),
                     "latest_test": last["at"].date().isoformat(), "gap_sm3d": _r(max(gap, 0.0), 1), "cause": cause,
                     "evidence": evidence, "deferred_oil_sm3": round(d["lost_oil_sm3"]) if d else 0,
                     "record": last["record"], "counts": counts})
    if not rows:
        raise CalcError("no oil well in scope has enough tests for a decline fit")
    by = {c: {"wells": 0, "gap_sm3d": 0.0} for c in CAUSES}
    for r in rows:
        if r.pop("counts") or r["cause"] == "recorded deferment":
            by[r["cause"]]["wells"] += 1
            by[r["cause"]]["gap_sm3d"] += r["gap_sm3d"]
    total = sum(b["gap_sm3d"] for b in by.values())
    by_cause = [{"cause": c, "wells": b["wells"], "gap_sm3d": _r(b["gap_sm3d"], 1),
                 "share_pct": _r(b["gap_sm3d"] / total * 100, 1) if total else None} for c, b in by.items() if b["wells"]]
    by_cause.sort(key=lambda b: -(b["gap_sm3d"] or 0))
    unexplained = by["nothing on record"]["gap_sm3d"]
    rows.sort(key=lambda r: -(r["gap_sm3d"] or 0))
    return result(_r(total, 1), 0.0, "unexplained_shortfall" if total and unexplained / total >= 0.3 else None,
                  f"per oil well, its latest test before {at[:10]} against the Arps decline fitted through the tests before it "
                  "(Sm3/d oil), one cause "
                  f"each: EC deferment against the well in the {days} days before, else a suspect test (inflow check), "
                  "back-pressure or choke (its constraint now), lift (its gas lift curve), else nothing on record; "
                  f"shortfalls under {min_gap_sm3d:g} Sm3/d count only with recorded deferment",
                  [*(r["record"] for r in rows[:5])] or [fid or "asset"], unit="Sm3/d oil", at=at[:10],
                  by_cause=by_cause, unexplained_sm3d=_r(unexplained, 1), wells_checked=len(rows),
                  wells=rows[:20], unchecked=unchecked[:10])
