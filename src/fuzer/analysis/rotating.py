"""Rotating equipment analysis (#474): what an experienced machinery engineer checks first, as calcs ({value,
expected, flag, basis, sources}) the maintenance and integrity specialist cites.

- `vibration(ref)`: overall vibration (mm/s RMS) against the ISO 10816-3 / 20816-3 zones for the machine's group
  and foundation, its trend over the last 30 days and, when rising, the date it reaches zone D at that rate. Flags
  `zone_d` (damage likely: act now), `zone_c` (not for long-term running: plan the repair), `rising`.
- `pump_bep(ref)`: where a pump runs against its best efficiency point: the share of running time inside the
  preferred operating region (70-120 % of BEP flow, API 610), in the allowable region, and below the minimum
  continuous stable flow (recirculation, the seal and bearing killer). Flags `low_flow`, `off_bep`.
- `health(ref)`: one machine's health summary: every check that applies (curve, operating region, vibration,
  efficiency, failure history and its trend, peers), each finding with its basis, the dates of its data and a
  confidence, then a status (`act`, `watch`, `ok`) and recommendations. A check that cannot run says why (no OEM
  curve, no vibration point), so the summary shows what is unknown rather than leaving it out.
- `screen(at)`: every rotating machine (ISO 14224 compressors, pumps, gas turbines, motors, expanders, blowers) run
  through `health` at one date, `act` first, then by how many checks flag it. It finds the faulty machine without
  being told which one to look at; a flag every unit of a class shares (`class_wide_failures`) is reported once
  for the class rather than as a reason to look at each machine.

Spectra (bearing defect frequencies, 1x and 2x running speed) need a condition-monitoring source; `spectrum()` is the
interface it plugs into and says so until one is connected.
"""

from datetime import datetime, timedelta

import numpy as np

from .calcs import CalcError, _label, read, result, window_of

# ISO 10816-3 / 20816-3 zone boundaries, mm/s RMS: (A/B, B/C, C/D). Group 1: large machines (300 kW to 50 MW,
# compressors, turbines, large pumps); group 2: medium (15 to 300 kW, most pumps and fans).
ZONES = {("group1", "rigid"): (2.3, 4.5, 7.1), ("group1", "flexible"): (3.5, 7.1, 11.0),
         ("group2", "rigid"): (1.4, 2.8, 4.5), ("group2", "flexible"): (2.3, 4.5, 7.1)}
LARGE = ("compressor", "turbine", "generator", "expander", "blower")
POR = (0.70, 1.20)  # API 610 preferred operating region, fraction of BEP flow
AOR = (0.50, 1.30)  # allowable operating region when the datasheet gives none


def _tag(conn, fid: str) -> str:
    """The tag a unit is installed at (readings hang off the tag), or the tag itself."""
    k = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()
    if k is None:
        raise CalcError("unknown entity")
    if k["kind"] == "equipment":
        r = conn.execute("select dst from edge where rel = 'installed_at' and src = %s and valid_to is null",
                         (fid,)).fetchone()
        return r["dst"] if r else fid
    return fid


def _describe(conn, fid: str) -> str:
    """What the machine is, in words: its description, class and model, for the group and the checks that apply."""
    rows = conn.execute(
        """
        select coalesce(g.value #>> '{}', '') as d from golden g where g.fuzer_id = %(f)s and g.field = 'description'
        union all
        select coalesce(l.label, '') from edge e join entity_label l on l.fuzer_id = e.dst
        where e.src = %(f)s and e.rel in ('classified_as', 'instance_of')
        """, {"f": fid}).fetchall()
    return " ".join(r["d"] for r in rows).lower()


def machine_group(conn, fid: str) -> str:
    return "group1" if any(w in _describe(conn, fid) for w in LARGE) else "group2"


def zone_of(v: float, bounds: tuple[float, float, float]) -> str:
    return "A" if v < bounds[0] else "B" if v < bounds[1] else "C" if v < bounds[2] else "D"


def vibration(conn, fid: str, days: int = 90, until: str | None = None, group: str | None = None,
              foundation: str = "rigid", src=None) -> dict:
    from ..sources.timeseries import _unit_points

    tag = _tag(conn, fid)
    pts = [p for p in _unit_points(conn, tag, 30) if (p["uom"] or "").lower() == "mm/s"]
    if not pts:
        raise CalcError("no vibration point (mm/s) on that machine")
    group = group or machine_group(conn, tag)
    bounds = ZONES[(group, foundation)]
    start, end = window_of(conn, days, until)
    v = read({p["id"]: p for p in pts}, start, end, 360, src)
    worst = None
    for p in pts:
        series = v[p["id"]]
        if len(series) < 10:
            continue
        daily: dict[str, list[float]] = {}
        for t, x in series.items():
            daily.setdefault(t[:10], []).append(x)
        days_ = sorted(daily)
        med = [float(np.median(daily[d])) for d in days_]
        now = float(np.median(med[-3:]))
        recent = [(i, m) for i, m in enumerate(med) if days_[i] >= (end - timedelta(days=30)).date().isoformat()]
        slope = r2 = 0.0
        if len(recent) >= 7:
            xs, ys = np.array([float(i) for i, _ in recent]), np.array([m for _, m in recent])
            slope, icpt = np.polyfit(xs, ys, 1)
            ss = float(np.sum((ys - ys.mean()) ** 2))
            r2 = 1 - float(np.sum((ys - (slope * xs + icpt)) ** 2)) / ss if ss else 0.0
            slope = float(slope)
        row = {"point": p["point"], "id": p["id"], "description": p["description"], "now": now,
               "per_week": slope * 7, "r2": r2, "daily": [{"day": d, "mm_s": round(m, 2)} for d, m in zip(days_, med)],
               "first": days_[0], "last": days_[-1], "readings": len(series)}
        if worst is None or now > worst["now"]:
            worst = row
    if worst is None:
        raise CalcError("the vibration points have too few readings in that window")
    zone = zone_of(worst["now"], bounds)
    rising = worst["per_week"] > 0.05 * bounds[1] and worst["r2"] >= 0.5
    to_d = None
    if worst["per_week"] > 0 and zone != "D":
        to_d = (datetime.fromisoformat(worst["last"]) + timedelta(weeks=(bounds[2] - worst["now"]) / worst["per_week"]))
    flag = "zone_d" if zone == "D" else "zone_c" if zone == "C" else "rising" if rising else None
    return result(round(worst["now"], 2), bounds[0], flag,
                  f"overall vibration, median of the last 3 days of daily medians, on the highest of {len(pts)} "
                  f"mm/s point(s), against ISO 10816-3 {group.replace('group', 'group ')} {foundation} zones "
                  f"(A/B {bounds[0]}, B/C {bounds[1]}, C/D {bounds[2]} mm/s); trend fitted to the last 30 days",
                  [tag, worst["id"]], unit="mm/s RMS", zone=zone, zones={"A/B": bounds[0], "B/C": bounds[1],
                                                                        "C/D": bounds[2]},
                  group=group, foundation=foundation, point=worst["point"], trend_mm_s_per_week=round(worst["per_week"], 3),
                  trend_r2=round(worst["r2"], 2), reaches_zone_d=to_d.date().isoformat() if to_d and rising else None,
                  data_from=worst["first"], data_to=worst["last"], readings=worst["readings"], daily=worst["daily"][-60:])


def pump_bep(conn, fid: str, days: int = 90, until: str | None = None, low_flow_share: float = 0.2) -> dict:
    from .calcs import pump_operation

    p = pump_operation(conn, fid, days, until=until)
    bep_q = (p["bep"] or [None])[0]
    if not bep_q:
        raise CalcError("the OEM curve gives no best efficiency point")
    ratings = p["curve"].get("ratings") or {}
    mcsf = (ratings.get("mcsf") or [None])[0] or 0.3 * bep_q
    aor = ratings.get("aor") or [AOR[0] * bep_q, AOR[1] * bep_q]
    q = np.array([x[1] for x in p["ops"]], float)
    frac = q / bep_q
    por_share = float(np.mean((frac >= POR[0]) & (frac <= POR[1])))
    aor_share = float(np.mean((q >= aor[0]) & (q <= aor[1])))
    low_share = float(np.mean(q < max(mcsf, AOR[0] * bep_q)))
    cut = (datetime.fromisoformat(p["ops"][-1][0]) - timedelta(days=30)).isoformat()
    recent = [x[1] for x in p["ops"] if x[0] >= cut] or list(q)
    now = float(np.median(recent)) / bep_q * 100
    flag = "low_flow" if low_share >= low_flow_share else "off_bep" if not POR[0] * 100 <= now <= POR[1] * 100 else None
    return result(round(now, 1), 100.0, flag,
                  f"flow while running against the {p['curve']['source']} BEP ({bep_q:g}); preferred region "
                  f"{int(POR[0] * 100)}-{int(POR[1] * 100)} % of BEP flow (API 610), allowable {aor[0]:g}-{aor[1]:g}, "
                  f"minimum stable flow {mcsf:g}; over {len(p['ops'])} readings in {days} days",
                  [p["tag"], p["curve"]["model_id"], *p["need"]], unit="% of BEP flow",
                  in_preferred_pct=round(por_share * 100, 1), in_allowable_pct=round(aor_share * 100, 1),
                  below_min_flow_pct=round(low_share * 100, 1), bep_flow=bep_q,
                  data_from=p["ops"][0][0][:10], data_to=p["ops"][-1][0][:10], readings=len(p["ops"]))


def spectrum(conn, fid: str) -> dict:
    """Bearing defect frequencies and 1x / 2x running speed need a condition-monitoring source (spectra, waveforms,
    bearing geometry). None is connected; this is where one plugs in."""
    raise CalcError("no condition-monitoring source is connected: overall vibration only (see vibration)")


# ---- the health summary ------------------------------------------------------------------------------------------

SEVERE = {"zone_d", "head_loss", "failures_accelerating", "low_flow"}
ACTIONS = {
    "zone_d": "Vibration is in zone D: plan to stop and inspect now (bearings, alignment, looseness).",
    "zone_c": "Vibration is in zone C: not for long-term running; plan the repair at the next window and trend it daily.",
    "rising": "Vibration is rising steadily: check lubrication and alignment, and trend it until the cause is found.",
    "head_loss": "Head is below the OEM curve at the same flow: internal wear (wear rings, impeller); plan an overhaul.",
    "low_flow": "It runs below its minimum stable flow too often: recirculation damages seals and bearings; "
                "check the minimum-flow bypass and the process demand, or resize.",
    "off_bep": "It runs away from its best efficiency point: shorter seal and bearing life; check the operating envelope.",
    "efficiency_drop": "Polytropic efficiency has dropped: fouling, wear, or recycle through a hot cooler; "
                       "check the anti-surge valve and the cooler before it.",
    "wear_out": "Failures follow a wear-out pattern: a time-based PM interval below eta would help.",
    "early_life": "Failures cluster early after repair: look at installation and repair quality.",
    "failures_accelerating": "Failures are coming faster: a bad actor; root-cause the repeat failure mode.",
    "outliers": "It is an outlier in its class: the unit, not the model.",
    "class_wide_failures": "The whole class fails often: the model or its service.",
}


def _confidence(check: str, r: dict) -> str:
    n = r.get("readings") or r.get("failures") or r.get("n") or 0
    if check == "reliability":
        return "medium" if (r.get("failures") or 0) >= 6 else "low"
    if check in ("vibration", "pump_bep", "pump_curve", "compressor_performance"):
        return "high" if n >= 100 or check in ("pump_curve", "compressor_performance") else "medium"
    return "medium"


def health(conn, fid: str, until: str | None = None) -> dict:
    """One machine's health summary: every check that applies, each with basis, data dates and confidence."""
    from . import equipment
    from .calcs import pump_curve, reliability

    tag = _tag(conn, fid)
    what = _describe(conn, tag)
    checks = []
    if "pump" in what:
        checks += [("pump_curve", lambda: pump_curve(conn, tag, until=until)),
                   ("pump_bep", lambda: pump_bep(conn, tag, until=until))]
    if "compressor" in what:
        checks.append(("compressor_performance", lambda: equipment.compressor_performance(conn, tag, until=until)))
    checks += [("vibration", lambda: vibration(conn, tag, until=until)),
               ("reliability", lambda: reliability(conn, tag, "weibull", until=until)),
               ("failure_trend", lambda: reliability(conn, tag, "crow_amsaa", until=until)),
               ("peers", lambda: equipment.peers(conn, tag))]
    findings, unknown = [], []
    for name, fn in checks:
        try:
            r = fn()
        except CalcError as e:
            unknown.append({"check": name, "why": str(e)})
            continue
        findings.append({"check": name, "flag": r.get("flag"), "value": r.get("value"), "expected": r.get("expected"),
                         "unit": r.get("unit"), "basis": r["basis"], "sources": r["sources"],
                         "data": {k: r[k] for k in ("data_from", "data_to", "at", "first", "last") if r.get(k)},
                         "confidence": _confidence("reliability" if name == "failure_trend" else name, r),
                         **{k: r[k] for k in ("zone", "reaches_zone_d", "trend_mm_s_per_week", "in_preferred_pct",
                                              "below_min_flow_pct", "reading", "eta_days", "worst_month")
                            if r.get(k) is not None}})
    flags = [f["flag"] for f in findings if f["flag"]]
    status = "act" if any(f in SEVERE for f in flags) else "watch" if flags else "ok"
    recs = [ACTIONS[f] for f in dict.fromkeys(flags) if f in ACTIONS]
    if not findings:
        raise CalcError("no check could run on that machine: " + "; ".join(u["why"] for u in unknown))
    return {"value": status, "expected": "ok", "deviation": None, "deviation_pct": None,
            "flag": None if status == "ok" else status,
            "basis": f"{len(findings)} check(s) on {_label(conn, tag)}: " + ", ".join(f["check"] for f in findings),
            "sources": [tag, *dict.fromkeys(s for f in findings for s in f["sources"])], "unit": "status",
            "machine": _label(conn, tag), "findings": findings, "unknown": unknown,
            "recommendations": recs or ["Nothing to act on in the data Fuzer has."]}




ROTATING_CLASSES = r"^ISO14224:(CO|PU|GT|EM|TE|BL|ST)([.]|$)"
_RANK = {"act": 0, "watch": 1, "ok": 2}


def screen(conn, until: str | None = None, limit: int = 20) -> dict:
    """Every rotating machine's health at `until`, worst first: the fleet screen behind "which machines need
    attention?". Class-wide flags are lifted out to `class_wide`, so a machine is `watch` for its own reasons."""
    tags = conn.execute(
        """
        select distinct i.dst as tag, cl.label as cls from edge i
        join edge c on c.src = i.src and c.rel = 'classified_as'
        join entity_label cl on cl.fuzer_id = c.dst
        where i.rel = 'installed_at' and i.valid_to is null and cl.label ~ %s
        """, (ROTATING_CLASSES,)).fetchall()
    machines, unchecked, class_wide = [], [], set()
    for t in tags:
        try:
            h = health(conn, t["tag"], until=until)
        except CalcError as e:
            unchecked.append({"machine": _label(conn, t["tag"]), "why": str(e)})
            continue
        own = [f for f in h["findings"] if f["flag"] and f["flag"] != "class_wide_failures"]
        if any(f["flag"] == "class_wide_failures" for f in h["findings"]):
            class_wide.add(t["cls"])
        status = "act" if any(f["flag"] in SEVERE for f in own) else "watch" if own else "ok"
        machines.append({"id": t["tag"], "machine": h["machine"], "status": status,
                         "flags": [f["flag"] for f in own], "unknown": [u["check"] for u in h["unknown"]],
                         "recommendations": [ACTIONS[f["flag"]] for f in own if f["flag"] in ACTIONS]})
    machines.sort(key=lambda m: (_RANK[m["status"]], -len(m["flags"]), m["machine"]))
    counts = {k: sum(1 for m in machines if m["status"] == k) for k in _RANK}
    return {"at": until, "machines_checked": len(machines), "by_status": counts,
            "machines": machines[:limit], "unchecked": unchecked,
            "class_wide_failures": sorted(class_wide),
            "basis": "rotating equipment health (curve, operating region, vibration zone and trend, efficiency, "
                     "Weibull and Crow-AMSAA on the failure history, peers) for every ISO 14224 rotating class; "
                     "act = a severe flag (" + ", ".join(sorted(SEVERE)) + ")"}
