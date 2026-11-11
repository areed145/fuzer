"""Rotating equipment analysis (#474): what an experienced machinery engineer checks first, as calcs ({value,
expected, flag, basis, sources}) the maintenance and integrity specialist cites.

- `vibration(ref)`: overall vibration (mm/s RMS) against the ISO 20816-3 (formerly 10816-3) zones for the machine's group
  and foundation, its trend over the last 30 days and, when rising, the date it reaches zone D at that rate. Flags
  `zone_d` (damage likely: act now), `zone_c` (not for long-term running: plan the repair), `rising`.
- `pump_bep(ref)`: where a pump runs against its best efficiency point: the share of running time inside the
  preferred operating region (70-120 % of BEP flow, API 610), in the allowable region, and below the minimum
  continuous stable flow (recirculation, the seal and bearing killer). Flags `low_flow`, `off_bep`.
- `surge(ref)`: a centrifugal compressor stage against its own OEM map: surge margin from the suction flow and the
  driver's speed, how often the anti-surge valve is open, head against the map at the same flow and speed, and the
  same stage on the other trains. Flags `near_surge` (the last week's 5th percentile margin under 5 %) and
  `recycling` (the valve open more than 30 % of the time). A machine without its map says so.
- `heat_rate(ref)`: a gas turbine driver's heat rate (fuel energy per kWh out) against its OEM curves at the same
  load and ambient temperature, and its weekly trend. Flags `heat_rate_high` at 4 % or more above expected.
- `motor_load(ref)`: an electric motor driver's measured power against its nameplate rating and service factor (the
  motor's rated kW and service factor on the driven item's SAP equipment record), with the running hours above the
  rating and above the service factor. Flags `motor_overload` (the last week runs above the service factor:
  insulation life is being spent) and `motor_high` (above the rating, inside the service factor).
- `health(ref)`: one machine's health summary: every check that applies (curve, operating region, vibration,
  efficiency, failure history and its trend, peers), each finding with its basis, the dates of its data and a
  confidence, then a status (`act`, `watch`, `ok`) and recommendations. A check that cannot run says why (no OEM
  curve, no vibration point), so the summary shows what is unknown rather than leaving it out.
- `screen(at)`: every rotating machine (ISO 14224 compressors, pumps, gas turbines, motors, expanders, blowers) run
  through `health` at one date, `act` first, then by how many checks flag it. It finds the faulty machine without
  being told which one to look at; a flag every unit of a class shares (`class_wide_failures`) is reported once
  for the class rather than as a reason to look at each machine.

- `watch(at)`: the screen raised for the IOC: one `machine_health` exception per machine whose condition is flagged,
  routed to the engineers' queue, updated while it holds and cleared (closing its case) when it doesn't. Failure-
  history flags alone stay with the reliability review.

- `surge_events(ref)`: outright surges on a centrifugal stage from their signature (the suction flow collapsing as the
  anti-surge valve snaps open), with the thrust kick each gave. Flags `surge_events`.

`health` also runs the API 670 protection channels and the dry gas seal (`protection`), the lube oil system, and for
a gas turbine its exhaust spread, hot section hours and the train's power balance (`drivers`, `compression`); a
machine the screen raises carries its remaining life against the next window (`prognosis`).

Reciprocating compressors (ISO 14224 CO.RE) take `recip.rod_load` and `recip.valve_health` in place of the
centrifugal checks, and their vibration is judged against the maker's alarm and trip.

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


def is_recip(conn, tag: str) -> bool:
    """A reciprocating compressor (ISO 14224 CO.RE) is installed at the tag."""
    return bool(conn.execute(
        """
        select 1 from edge i join edge c on c.src = i.src and c.rel = 'classified_as'
        join entity_label cl on cl.fuzer_id = c.dst
        where i.dst = %s and i.rel = 'installed_at' and i.valid_to is null and cl.label ~ '^ISO14224:CO[.]RE' limit 1
        """, (tag,)).fetchone())


def recip_bounds(conn, point_ids: list[str]) -> tuple[float, float, float]:
    """Zone boundaries for a recip's frame vibration from its maker's alarm (hi) and trip (hihi) on the points."""
    row = conn.execute("select min(value) filter (where limit_type = 'hi') as hi, min(value) filter (where limit_type = 'hihi') as hihi "
                       "from point_limit where point_id = any(%s) and enabled", (point_ids,)).fetchone()
    if not row or row["hi"] is None or row["hihi"] is None:
        raise CalcError("no frame vibration alarm and trip on record for this reciprocating compressor (its datasheet "
                        "gives them); the centrifugal zones don't apply")
    return (round(row["hi"] / 2, 2), float(row["hi"]), float(row["hihi"]))


def zone_of(v: float, bounds: tuple[float, float, float]) -> str:
    return "A" if v < bounds[0] else "B" if v < bounds[1] else "C" if v < bounds[2] else "D"


def vibration(conn, fid: str, days: int = 90, until: str | None = None, group: str | None = None,
              foundation: str = "rigid", src=None) -> dict:
    from ..sources.timeseries import _unit_points

    tag = _tag(conn, fid)
    pts = [p for p in _unit_points(conn, tag, 30) if (p["uom"] or "").lower() == "mm/s"]
    if not pts:
        raise CalcError("no vibration point (mm/s) on that machine")
    recip = is_recip(conn, tag)
    if recip:  # ISO 20816-3's zones are for rotating machines: a recip's frame is judged against its maker's limits
        bounds = recip_bounds(conn, [p["id"] for p in pts])
        group = "reciprocating"
    else:
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
                  f"mm/s point(s), against "
                  + (f"the maker's frame vibration alarm ({bounds[1]:g}) and trip ({bounds[2]:g} mm/s) for a "
                     f"reciprocating compressor (ISO 20816-8; zone A/B at half the alarm)" if recip else
                     f"ISO 20816-3 (formerly ISO 10816-3) {group.replace('group', 'group ')} {foundation} zones")
                  + f" (A/B {bounds[0]:g}, B/C {bounds[1]:g}, C/D {bounds[2]:g} mm/s); trend fitted to the last 30 days",
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


NEAR_SURGE = 5.0  # % surge margin: the 5th percentile of the last week under this is a surge risk
RECYCLING = 0.3  # share of running time with the anti-surge valve open: recycling, not compressing for the process


def _stage_map(conn, tag: str) -> dict:
    from ..sources import curves

    c = curves.for_machine(conn, tag)
    if not c or c.get("curve") != "compressor" or not c["series"].get("surge"):
        raise CalcError("no OEM compressor map for this machine (maps are issued per machine), so no surge line")
    return c


def _surge_flow(c: dict, speed_pct) -> np.ndarray:
    """Flow at surge at a speed, on the map's surge line (fan law, flow with speed, beyond its ends)."""
    pts = sorted(c["series"]["surge"])
    xs, ys = np.array([p[0] for p in pts], float), np.array([p[1] for p in pts], float)
    s = np.asarray(speed_pct, float)
    return np.where(s < xs[0], ys[0] * s / xs[0], np.where(s > xs[-1], ys[-1] * s / xs[-1], np.interp(s, xs, ys)))


def _map_head(c: dict, q: float, speed_pct: float) -> float | None:
    """Polytropic head on the map at a flow and speed: the rated speed line scaled by the fan laws."""
    from ..sources import curves

    line = c["series"].get("head@100")
    if not line:
        return None
    s = speed_pct / 100
    h = curves.at(line, q / s)
    return None if h is None else h * s * s


def _stage_parts(conn, tag: str) -> dict:
    """A compressor stage's suction flow and anti-surge valve points, and its driver's speed (20-K-2100C is
    driven by 20-GT-2000C)."""
    import re

    from .equipment import _tag_point

    label = _label(conn, tag)
    m = re.match(r"^(\d\d)-K-(\d)\d00([A-Z])$", label)
    if not m:
        raise CalcError("not a compressor stage tag (NN-K-N?00X)")
    area, series, train = m.groups()
    parts = {"flow": _tag_point(conn, label, "Suction Flow"), "asv": _tag_point(conn, label, "Anti-Surge Valve"),
             "speed": _tag_point(conn, f"{area}-GT-{series}000{train}", "Speed")}
    missing = [k for k, v in parts.items() if not v]
    if missing:
        raise CalcError("no " + " or ".join({"flow": "suction flow", "asv": "anti-surge valve", "speed": "driver speed"}[k]
                                            for k in missing) + " point for that stage")
    return parts


def surge(conn, fid: str, days: int = 30, until: str | None = None, recent_days: int = 7, src=None) -> dict:
    """Where a centrifugal compressor stage runs against its own OEM map: surge margin ((Q / Q surge at the
    running speed) - 1, from the suction flow and the driver's speed), how often the anti-surge valve is open
    (recycling), polytropic head against the map at the same flow and speed, and the same stage on the other trains
    (load sharing). Flags `near_surge` when the 5th percentile margin of the last week is under 5 %, `recycling`
    when the valve is open more than 30 % of the running time."""
    from . import equipment

    tag = _tag(conn, fid)
    c = _stage_map(conn, tag)
    parts = _stage_parts(conn, tag)
    rated = (c["ratings"].get("rated_speed") or [None])[0]
    design = c["ratings"].get("design") or [None, None]
    if not rated or not design[0]:
        raise CalcError("the map gives no rated speed or design point")
    start, end = window_of(conn, days, until)
    v = read(parts, start, end, 10, src)
    rows = [(t, q, v["speed"].get(t), v["asv"].get(t)) for t, q in sorted(v["flow"].items())]
    rows = [r for r in rows if r[2] is not None and r[1] > 0.2 * design[0] and r[2] > 0.5 * rated]  # running
    if len(rows) < 36:
        raise CalcError("the stage hardly ran in that window")
    cut = (datetime.fromisoformat(rows[-1][0]) - timedelta(days=recent_days)).isoformat()
    recent = [r for r in rows if r[0] >= cut] or rows
    speed = np.array([r[2] for r in recent], float) / rated * 100
    q = np.array([r[1] for r in recent], float)
    margin = (q / _surge_flow(c, speed) - 1) * 100
    asv = np.array([r[3] if r[3] is not None else 0 for r in recent], float)
    open_share = float(np.mean(asv > 3))
    p5, med = float(np.percentile(margin, 5)), float(np.median(margin))
    daily: dict[str, list] = {}
    for t, qq, sp, a in rows:
        daily.setdefault(t[:10], []).append((qq / float(_surge_flow(c, sp / rated * 100)) - 1) * 100)
    head = None
    try:  # head from the stage's pressures and temperatures, against the map at the same flow and speed
        perf = equipment.compressor_performance(conn, tag, days=days, until=until, src=src)
        mh = _map_head(c, float(np.median(q)), float(np.median(speed)))
        if mh:
            head = {"data_kj_kg": perf["head_kj_kg"], "map_kj_kg": round(mh, 1),
                    "vs_map_pct": round((perf["head_kj_kg"] / mh - 1) * 100, 1)}
    except CalcError:
        pass
    siblings = []
    label = _label(conn, tag)
    for other in conn.execute("select fuzer_id, label from entity_label where label ~ %s and label <> %s order by label",
                              (f"^{label[:-1]}[A-Z]$", label)).fetchall():
        try:
            o = _stage_parts(conn, other["fuzer_id"])
            ov = read({"flow": o["flow"]}, end - timedelta(days=recent_days), end, 60, src)["flow"]
            run = [x for x in ov.values() if x > 0.2 * design[0]]
            if run:
                siblings.append({"machine": other["label"], "flow": round(float(np.median(run)), 0)})
        except CalcError:
            continue
    flag = "near_surge" if p5 < NEAR_SURGE else "recycling" if open_share > RECYCLING else None
    return result(round(med, 1), None, flag,
                  f"surge margin (Q / Q surge - 1) from the suction flow and the driver's speed against the "
                  f"{c['source']} map's surge line for this machine; anti-surge valve open (> 3 %) share of running "
                  f"time; the last {recent_days} days, every 10 minutes while running. Near surge under {NEAR_SURGE:g} % "
                  f"(5th percentile), recycling above {RECYCLING * 100:g} % of the time",
                  [tag, c["model_id"], *[p["id"] for p in parts.values()]], unit="% surge margin",
                  margin_p5=round(p5, 1), margin_min=round(float(margin.min()), 1),
                  asv_open_pct=round(open_share * 100, 1),
                  asv_median_open=round(float(np.median(asv[asv > 3])), 1) if open_share else 0.0,
                  speed_pct=round(float(np.median(speed)), 1), flow=round(float(np.median(q)), 0),
                  surge_flow=round(float(np.median(_surge_flow(c, speed))), 0), design_flow=design[0], head=head,
                  siblings=siblings, data_from=rows[0][0][:10], data_to=rows[-1][0][:10], readings=len(rows),
                  daily=[{"day": d, "margin_pct": round(float(np.median(x)), 1), "min_pct": round(float(min(x)), 1)}
                         for d, x in sorted(daily.items())][-60:])


SNAP = 40.0  # %-points the anti-surge valve opens in one step on a surge detection
COLLAPSE = 0.3  # share of the forward flow lost in the same few minutes


def surge_events(conn, fid: str, days: int = 30, until: str | None = None, src=None) -> dict:
    """Outright surges on a centrifugal stage, from their signature: the suction flow collapses and the anti-surge
    valve snaps open (the controller's surge response) in the same few minutes, while the train runs. Each event with
    its time, the flow and valve before and after and, when the stage has an axial position channel, how far the
    thrust kicked. A surge reverses the rotor's thrust in a fraction of a second and loads the dry gas seals, so
    each one counts against the thrust bearing and the seals whatever the margin says afterwards. Flags
    `surge_events` when there was one or more in the window."""
    from .condition import points_like

    tag = _tag(conn, fid)
    parts = _stage_parts(conn, tag)
    start, end = window_of(conn, days, until)
    axial = next(iter(points_like(conn, tag, r"axial position|thrust position")), None)
    pts = {"flow": parts["flow"], "asv": parts["asv"], "speed": parts["speed"], **({"axial": axial} if axial else {})}
    v = read(pts, start, end, 5, src)
    ts = sorted(v["flow"])
    top = float(np.percentile(list(v["speed"].values()), 90)) if v["speed"] else 0.0
    events: list[dict] = []
    for i in range(2, len(ts)):
        t, a, b = ts[i], ts[i - 1], ts[i - 2]
        if (v["speed"].get(t) or 0) < 0.8 * top or (v["speed"].get(b) or 0) < 0.8 * top:
            continue  # starting or stopping: the valve opens and the flow falls anyway
        q0, q1 = v["flow"].get(b), min(v["flow"].get(a, np.inf), v["flow"][t])
        z0, z1 = v["asv"].get(b), max(v["asv"].get(a, -np.inf), v["asv"].get(t, -np.inf))
        if q0 is None or z0 is None or q0 <= 0:
            continue
        if z1 - z0 >= SNAP and (q0 - q1) / q0 >= COLLAPSE:
            if events and (datetime.fromisoformat(t) - datetime.fromisoformat(events[-1]["at"])).total_seconds() < 1800:
                continue  # the same surge cycle
            ev = {"at": b[:16], "flow_before": round(q0, 0), "flow_min": round(float(q1), 0),
                  "asv_before": round(z0, 1), "asv_after": round(float(z1), 1)}
            if axial:
                near = [v["axial"][x] for x in ts[max(0, i - 3):i + 3] if x in v["axial"]]
                before = [v["axial"][x] for x in ts[max(0, i - 15):i - 3] if x in v["axial"]]
                if near and before:
                    kick = min(near) - float(np.median(before))
                    ev["thrust_kick_mm"] = round(float(kick), 2)
            events.append(ev)
    recent = [e for e in events if e["at"] >= (end - timedelta(days=30)).isoformat()]
    return result(len(events), 0, "surge_events" if events else None,
                  f"surge signature: the suction flow down {COLLAPSE * 100:g} % or more and the anti-surge valve up "
                  f"{SNAP:g} %-points or more within 10 minutes, at full running speed, every 5 minutes over the last "
                  f"{days} days; cycles within 30 minutes count once. The axial position's kick when the stage has "
                  "a thrust probe", [tag, *[p["id"] for p in pts.values()]], unit="surges",
                  machine=_label(conn, tag), events=events, last_30_days=len(recent), last=events[-1]["at"] if events else None,
                  data_from=ts[0][:10] if ts else None, data_to=ts[-1][:10] if ts else None, readings=len(ts))


HEAT_RATE_HIGH = 4.0  # % above the ambient- and load-corrected expected heat rate: worth a water wash


def _ambient_point(conn) -> dict | None:
    """The site's ambient air temperature point, wherever it hangs."""
    r = conn.execute(
        """
        select e.fuzer_id from entity e join golden g on g.fuzer_id = e.fuzer_id and g.field = 'description'
        where e.kind = 'timeseries' and g.value #>> '{}' ilike '%%ambient air temperature%%' order by 1 limit 1
        """).fetchone()
    if not r:
        return None
    from ..sources.timeseries import _point as point

    return point(conn, r["fuzer_id"])


def heat_rate(conn, fid: str, days: int = 30, until: str | None = None, recent_days: int = 7, src=None) -> dict:
    """A gas turbine driver's heat rate (fuel energy per kWh of output) against what its OEM curves expect at the
    same load and ambient temperature. A rising excess is the air compressor fouling (an offline water wash
    recovers it) or hot section wear (it doesn't). Flags `heat_rate_high` when the last week runs 4 % or more above
    expected."""
    from ..sources import curves
    from .calcs import curve_for
    from .equipment import _tag_point

    tag = _tag(conn, fid)
    label = _label(conn, tag)
    c = curve_for(conn, curves.model_of(conn, tag), "gas turbine")
    need = ("power_ambient", "heat_rate_ambient", "heat_rate_load")
    if c.get("curve") != "gas_turbine" or any(not c["series"].get(k) for k in need):
        raise CalcError("no OEM gas turbine performance curves for this model")
    lhv = (c["ratings"].get("fuel_lhv") or [None])[0]
    parts = {"fuel": _tag_point(conn, label, "Fuel Gas Flow"), "power": _tag_point(conn, label, "Output Power"),
             "ambient": _ambient_point(conn)}
    missing = [k for k, v in parts.items() if not v]
    if missing or not lhv:
        raise CalcError("no " + ", ".join(missing or ["design fuel heating value"]) + " point for that driver")
    start, end = window_of(conn, days, until)
    v = read(parts, start, end, 60, src)
    iso_kw = float(np.interp(15, *zip(*sorted(c["series"]["power_ambient"]))))
    rows = []
    for t, f in sorted(v["fuel"].items()):
        pw, amb = v["power"].get(t), v["ambient"].get(t)
        if pw is None or amb is None or pw < 0.3 * iso_kw or f <= 0:
            continue  # stopped or idling
        avail = float(np.interp(amb, *zip(*sorted(c["series"]["power_ambient"]))))
        load = min(100.0, pw / avail * 100)
        expected = (float(np.interp(load, *zip(*sorted(c["series"]["heat_rate_load"]))))
                    * float(np.interp(amb, *zip(*sorted(c["series"]["heat_rate_ambient"])))))
        rows.append((t, f * lhv * 1000 / pw, expected, load))
    if len(rows) < 24:
        raise CalcError("the driver hardly ran in that window")
    cut = (datetime.fromisoformat(rows[-1][0]) - timedelta(days=recent_days)).isoformat()
    recent = [r for r in rows if r[0] >= cut] or rows
    actual, expected = float(np.median([r[1] for r in recent])), float(np.median([r[2] for r in recent]))
    excess = (actual / expected - 1) * 100
    daily: dict[str, list] = {}
    for t, a, e, _ in rows:
        daily.setdefault(t[:10], []).append((a / e - 1) * 100)
    days_ = sorted((d, float(np.median(x))) for d, x in daily.items())
    xs = np.arange(len(days_), dtype=float)
    slope = float(np.polyfit(xs, [d[1] for d in days_], 1)[0]) * 7 if len(days_) >= 7 else None
    return result(round(actual, 0), round(expected, 0), "heat_rate_high" if excess >= HEAT_RATE_HIGH else None,
                  f"heat rate = fuel gas flow x the design fuel's LHV ({lhv:g} MJ/Sm3) / output power, hourly while "
                  f"loaded, against the {c['source']} curves' heat rate at the same load and ambient temperature; "
                  f"the last {recent_days} days' median. High at {HEAT_RATE_HIGH:g} % or more above expected",
                  [tag, c["model_id"], *[p["id"] for p in parts.values()]], unit="kJ/kWh",
                  excess_pct=round(excess, 1), trend_pct_per_week=round(slope, 2) if slope is not None else None,
                  load_pct=round(float(np.median([r[3] for r in recent])), 1),
                  data_from=rows[0][0][:10], data_to=rows[-1][0][:10], readings=len(rows),
                  daily=[{"day": d, "excess_pct": round(x, 1)} for d, x in days_][-60:])


MOTOR_OVERLOAD_SHARE = 10.0  # % of the last week's running hours above the service factor: act
MOTOR_HIGH_SHARE = 25.0  # % of the last week's running hours above the rating: watch


def motor_rating(conn, tag: str) -> dict:
    """The nameplate of the motor driving the unit installed at `tag`: rated kW and service factor from that unit's
    SAP equipment record, with the record's id as the source. The rating follows the physical unit, so a swapped
    pump brings its own motor's rating."""
    rows = conn.execute(
        """
        select e.src as unit, g.field, g.value #>> '{}' as v from edge e
        join golden g on g.fuzer_id = e.src and g.field in ('motor_rated_kw', 'motor_service_factor')
        where e.rel = 'installed_at' and e.dst = %s and e.valid_to is null
        """, (tag,)).fetchall()
    out: dict = {}
    for r in rows:
        try:
            out[r["field"]] = float(r["v"])
        except (TypeError, ValueError):
            continue
        out["unit"] = r["unit"]
    if not out.get("motor_rated_kw"):
        raise CalcError("no motor rating (rated kW) on the equipment installed there")
    return {"rated_kw": out["motor_rated_kw"], "service_factor": out.get("motor_service_factor"), "unit": out["unit"]}


def motor_load(conn, fid: str, days: int = 30, until: str | None = None, recent_days: int = 7, src=None) -> dict:
    """An electric motor's measured power against its nameplate: the last week's median running load as a share of
    the rating, and the running hours above the rating and above the service factor over the window. Running above
    the service factor spends insulation life; a pump that suddenly draws more for the same duty is running out
    along its curve (a passing recirculation valve, a lost downstream restriction) or pumping a heavier fluid."""
    from .equipment import _tag_point

    tag = _tag(conn, fid)
    rating = motor_rating(conn, tag)
    rated, sf = rating["rated_kw"], rating["service_factor"]
    p = _tag_point(conn, _label(conn, tag), "Motor Power")
    if not p:
        raise CalcError("no motor power (kW) point on that machine")
    start, end = window_of(conn, days, until)
    v = read({"power": p}, start, end, 60, src)["power"]
    running = sorted((t, x) for t, x in v.items() if x > 0.1 * rated)
    if len(running) < 24:
        raise CalcError("the motor hardly ran in that window")
    cut = (datetime.fromisoformat(running[-1][0]) - timedelta(days=recent_days)).isoformat()
    recent = [x for t, x in running if t >= cut] or [x for _, x in running]
    limit = rated * (sf or 1.0)
    over_sf = [t for t, x in running if sf and x > limit]
    over_rated = [t for t, x in running if x > rated]
    share_sf = 100 * sum(1 for x in recent if sf and x > limit) / len(recent)
    share_rated = 100 * sum(1 for x in recent if x > rated) / len(recent)
    median = float(np.median(recent))
    flag = ("motor_overload" if share_sf >= MOTOR_OVERLOAD_SHARE else
            "motor_high" if share_rated >= MOTOR_HIGH_SHARE else None)
    daily: dict[str, list] = {}
    for t, x in running:
        daily.setdefault(t[:10], []).append(x)
    return result(round(median, 0), rated, flag,
                  f"measured motor power, hourly while running (above 10 % of rating), against the {rated:g} kW "
                  f"nameplate rating" + (f" and its {sf:g} service factor ({limit:g} kW)" if sf else
                                         " (no service factor on record: the rating is the limit)")
                  + f" from the SAP equipment record; the last {recent_days} days' median. Overload when "
                  f"{MOTOR_OVERLOAD_SHARE:g} % or more of those running hours are above the service factor; high "
                  f"when {MOTOR_HIGH_SHARE:g} % or more are above the rating",
                  [tag, rating["unit"], p["id"]], unit="kW", load_pct=round(median / rated * 100, 1),
                  rated_kw=rated, service_factor=sf, hours_above_rating=len(over_rated),
                  hours_above_service_factor=len(over_sf), recent_above_sf_pct=round(share_sf, 1),
                  first_above_service_factor=over_sf[0][:16] if over_sf else None,
                  data_from=running[0][0][:10], data_to=running[-1][0][:10], readings=len(running),
                  daily=[{"day": d, "load_pct": round(float(np.median(x)) / rated * 100, 1)}
                         for d, x in sorted(daily.items())][-60:])


def spectrum(conn, fid: str) -> dict:
    """Bearing defect frequencies and 1x / 2x running speed need a condition-monitoring source (spectra, waveforms,
    bearing geometry). None is connected; this is where one plugs in."""
    raise CalcError("no condition-monitoring source is connected: overall vibration only (see vibration)")


# ---- the health summary ------------------------------------------------------------------------------------------

SEVERE = {"zone_d", "head_loss", "failures_accelerating", "low_flow", "near_surge", "motor_overload", "rod_load_high",
          "thrust_wear", "bearing_hot", "shaft_vibration_high", "surge_events", "seal_leak", "lube_pressure_low",
          "exhaust_spread", "inspection_overdue", "cylinder_misfire"}
ACTIONS = {
    "zone_d": "Vibration is in zone D: plan to stop and inspect now (bearings, alignment, looseness).",
    "zone_c": "Vibration is in zone C: not for long-term running; plan the repair at the next window and trend it daily.",
    "rising": "Vibration is rising steadily: check lubrication and alignment, and trend it until the cause is found.",
    "head_loss": "Head is below the OEM curve at the same flow: internal wear (wear rings, impeller); plan an overhaul.",
    "low_flow": "It runs below its minimum stable flow too often: recirculation damages seals and bearings; "
                "check the minimum-flow bypass and the process demand, or resize.",
    "off_bep": "It runs away from its best efficiency point: shorter seal and bearing life; check the operating envelope.",
    "near_surge": "It runs close to surge: the margin dips under 5 % with the anti-surge valve open. Check the "
                  "anti-surge valve's response and the controller's tuning, and give this train more gas: shift load "
                  "from the other trains, or stop recycling by running one train fewer.",
    "recycling": "The anti-surge valve is open much of the time: it recycles gas it has already compressed, which "
                 "costs fuel. Rebalance the load between trains so this one runs off its control line.",
    "heat_rate_high": "It burns more fuel than its curves expect at this load and ambient: usually the air "
                      "compressor fouling; plan an offline water wash and check the inlet filters. If a wash doesn't "
                      "recover it, suspect hot section wear and plan a borescope.",
    "motor_overload": "Its motor runs above the nameplate's service factor: insulation life is being spent. Find "
                      "why the duty rose: for a pump, compare its flow with the export or header meter (a passing "
                      "minimum-flow recirculation valve shows as pump flow the meter doesn't see), then fix it or "
                      "share the load with the standby.",
    "motor_high": "Its motor runs above its rating, inside the service factor: no margin for a hot day or a "
                  "heavier fluid. Check the duty against the datasheet.",
    "efficiency_drop": "Polytropic efficiency has dropped: fouling, wear, or recycle through a hot cooler; "
                       "check the anti-surge valve and the cooler before it.",
    "wear_out": "Failures follow a wear-out pattern: a time-based PM interval below eta would help.",
    "early_life": "Failures cluster early after repair: look at installation and repair quality.",
    "failures_accelerating": "Failures are coming faster: a bad actor; root-cause the repeat failure mode.",
    "outliers": "It is an outlier in its class: the unit, not the model.",
    "rod_load_high": "A throw's gas rod load is past the frame's rating: crossheads, pins and rods are at risk. Cut "
                     "the load or the stage pressure now, and find why the stage pressures moved (a leaking valve on "
                     "the next stage pushes the interstage pressure up).",
    "no_reversal": "A throw's rod load hardly reverses: the crosshead pin is starved of oil. Change the load step or "
                   "the clearance pockets so the load reverses every revolution.",
    "valve_leak": "One cylinder runs hotter than the others on its stage: a leaking suction or discharge valve. Plan "
                  "a valve change on that cylinder; spares are the valve assembly on the datasheet.",
    "ratio_shift": "The pressure ratio between the stages has shifted: one stage lost capacity, most often a leaking "
                   "valve or worn rings on that stage. Check its cylinder temperatures and plan a valve inspection.",
    "class_wide_failures": "The whole class fails often: the model or its service.",
    "thrust_wear": "The thrust bearing is going: the rotor has walked toward its active pads and they run hot. Plan "
                   "the bearing change before the axial trip, cut the load meanwhile, and check what loads the thrust "
                   "(balance piston seal wear, surges).",
    "bearing_hot": "A journal bearing runs past its temperature alarm: check the lube oil supply pressure and "
                   "temperature and take an oil sample; if the oil is fine, the bearing is wearing.",
    "shaft_vibration_high": "Shaft vibration at the proximity probes is past its alarm: read it with the casing "
                            "vibration and the bearing temperatures; a rub, unbalance or bearing wear.",
    "protection_rising": "A protection channel is on course for its alarm within a month: trend it daily and plan the "
                         "inspection before it gets there.",
    "surge_events": "The stage has surged outright: each one reverses the thrust and loads the dry gas seals. Check "
                    "the anti-surge valve's stroke time and the controller's tuning, look at the thrust position and "
                    "seal vents after each event, and give the train more forward flow.",
    "seal_leak": "The dry gas seal's primary vent flow is past its alarm: the primary seal is failing and the "
                 "secondary is all that's left. Plan the seal cartridge change now; check the seal gas supply and its "
                 "filter, and the separation gas, in the meantime.",
    "seal_rising": "The dry gas seal's vent flow has doubled and keeps climbing: check the seal gas filter and supply "
                   "pressure, and order a seal cartridge.",
    "lube_pressure_low": "Lube oil header pressure is below its low alarm and still falling: change over to the "
                         "standby filter (or pump) now, before it trips on low oil pressure; check the filter's "
                         "differential pressure.",
    "lube_pressure_falling": "Lube oil header pressure is falling toward its low alarm: check the filter differential "
                             "and plan the changeover.",
    "lube_oil_hot": "Lube oil supply runs hot: check the oil cooler (fouling, fans, cooling water) and the "
                    "temperature control valve.",
    "power_mismatch": "The power the stages absorb no longer matches the driver's output: a flow meter, the power "
                      "estimate or a stage's efficiency has moved. Check the meters against each other first.",
    "exhaust_spread": "The exhaust temperature spread has widened: a combustor runs lean or hot (a plugged fuel "
                      "nozzle, a cracked liner) or a thermocouple failed. Check the thermocouple named, then borescope "
                      "the combustor behind it before the hot section is damaged.",
    "inspection_overdue": "The hot section inspection is past its fired hours: run on borrowed time only with the "
                          "maker's extension; schedule it now.",
    "inspection_due": "The hot section inspection falls due soon on fired hours: book the outage (or the maker's "
                      "extension) now, and line it up with the next window if it reaches it.",
    "cylinder_misfire": "An engine cylinder runs cold: it is misfiring. Check its spark plug, coil and gas valve; a "
                        "misfire costs power and can damage the catalyst and the crankshaft.",
    "cylinder_hot": "An engine cylinder runs hot: overload or detonation. Check its ignition timing and the fuel "
                    "quality, and balance the cylinders.",
}


def _confidence(check: str, r: dict) -> str:
    n = r.get("readings") or r.get("failures") or r.get("n") or 0
    if check == "reliability":
        return "medium" if (r.get("failures") or 0) >= 6 else "low"
    if check in ("vibration", "pump_bep", "pump_curve", "compressor_performance", "surge", "heat_rate", "motor_load",
                 "rod_load", "valve_health", "protection", "seal_gas", "lube_oil", "surge_events", "power_balance",
                 "exhaust_spread", "engine_cylinders"):
        return "high" if n >= 100 or check in ("pump_curve", "compressor_performance", "heat_rate") else "medium"
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
                   ("pump_bep", lambda: pump_bep(conn, tag, until=until)),
                   ("motor_load", lambda: motor_load(conn, tag, until=until))]
    from . import compression, drivers, protection
    if is_recip(conn, tag):
        from . import recip
        checks += [("rod_load", lambda: recip.rod_load(conn, tag, until=until)),
                   ("valve_health", lambda: recip.valve_health(conn, tag, until=until)),
                   ("lube_oil", lambda: protection.lube_oil(conn, tag, until=until)),
                   ("engine_cylinders", lambda: drivers.engine_cylinders(conn, tag, until=until))]
    elif "compressor" in what:
        checks += [("compressor_performance", lambda: equipment.compressor_performance(conn, tag, until=until)),
                   ("surge", lambda: surge(conn, tag, until=until)),
                   ("surge_events", lambda: surge_events(conn, tag, until=until)),
                   ("protection", lambda: protection.protection(conn, tag, until=until)),
                   ("seal_gas", lambda: protection.seal_gas(conn, tag, until=until))]
    if "turbine" in what:
        checks += [("heat_rate", lambda: heat_rate(conn, tag, until=until)),
                   ("exhaust_spread", lambda: drivers.exhaust_spread(conn, tag, until=until)),
                   ("hot_section", lambda: drivers.hot_section(conn, tag, until=until)),
                   ("lube_oil", lambda: protection.lube_oil(conn, tag, until=until)),
                   ("power_balance", lambda: compression.power_balance(conn, tag, until=until))]
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
                                              "below_min_flow_pct", "reading", "eta_days", "worst_month", "margin_p5", "excess_pct",
                                              "load_pct", "hours_above_service_factor",
                                              "asv_open_pct", "head", "siblings", "pct_of_rating", "worst_throw",
                                              "hottest_cylinder", "weak_stage", "reversal_ratio", "stage_ratio",
                                              "worst_channel", "times_clean", "reaches_high_high", "wandered",
                                              "used_pct", "due", "cylinder", "last_30_days", "shift_pct")
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
            "basis": "rotating equipment health (curve, operating region, motor load, surge margin, heat rate, vibration zone and trend, efficiency, "
                     "rod load and cylinder valves on recips, API 670 protection channels, dry gas seals, lube oil, "
                     "surges, gas turbine exhaust spread and hot section hours, engine cylinders, "
                     "Weibull and Crow-AMSAA on the failure history, peers) for every ISO 14224 rotating class; "
                     "act = a severe flag (" + ", ".join(sorted(SEVERE)) + ")"}


# ---- raising them: the fleet screen as work for the IOC ----------------------------------------------------------

def _health_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("machine_health") or exception_type(
        "machine_health", "Machine health", role="role:engineer", audience=("ioc", "field"),
        description="A rotating machine the fleet screen flags: vibration in zone C or D, near surge, a motor "
                    "past its service factor, head or efficiency loss, a valve or rod load problem. It clears, "
                    "closing its case, when the screen no longer flags it.")


HEALTH_TYPE = _health_type().name
PRIORITY = {"act": 2.0, "watch": 0.5}
# Which severe finding leads a machine's title and recommendation: the one that ends the machine soonest.
FIRST = ["zone_d", "thrust_wear", "bearing_hot", "rod_load_high", "motor_overload", "near_surge", "surge_events",
         "seal_leak", "lube_pressure_low", "exhaust_spread", "cylinder_misfire", "inspection_overdue", "head_loss",
         "low_flow", "shaft_vibration_high"]
# Failure-history findings are for the reliability review (bad actors, PM intervals), not a condition to act on now:
# the screen reports them, the queue gets the machines whose condition is flagged.
HISTORY = {"wear_out", "early_life", "failures_accelerating", "outliers", "class_wide_failures"}
WORDS = {"zone_d": "vibration in zone D", "zone_c": "vibration in zone C", "rising": "vibration rising",
         "head_loss": "head below its curve", "low_flow": "running below minimum flow", "off_bep": "running off BEP",
         "near_surge": "running near surge", "recycling": "recycling on its anti-surge valve",
         "heat_rate_high": "heat rate above its curves", "motor_overload": "motor above its service factor",
         "motor_high": "motor above its rating", "efficiency_drop": "efficiency dropped",
         "wear_out": "failures in a wear-out pattern", "early_life": "failures early after repair",
         "failures_accelerating": "failures coming faster", "outliers": "an outlier in its class",
         "rod_load_high": "rod load past its limit", "no_reversal": "rod load not reversing",
         "valve_leak": "a leaking cylinder valve", "ratio_shift": "stage pressure ratios shifted",
         "thrust_wear": "thrust bearing wearing", "bearing_hot": "a bearing past its temperature alarm",
         "shaft_vibration_high": "shaft vibration past its alarm", "protection_rising": "a protection channel rising",
         "surge_events": "surged outright", "seal_leak": "dry gas seal vent past its alarm",
         "seal_rising": "dry gas seal vent climbing", "lube_pressure_low": "lube oil pressure below its alarm",
         "lube_pressure_falling": "lube oil pressure falling", "lube_oil_hot": "lube oil hot",
         "power_mismatch": "power balance shifted", "exhaust_spread": "exhaust temperature spread widened",
         "inspection_overdue": "hot section inspection overdue", "inspection_due": "hot section inspection due",
         "cylinder_misfire": "an engine cylinder misfiring", "cylinder_hot": "an engine cylinder running hot"}


def _prognosis(conn, fid: str, until: str | None) -> dict:
    """The remaining life and the next window, in words, for a machine the screen raises: will it make the
    turnaround? Empty when no trend on it heads for a limit."""
    from . import prognosis

    try:
        r = prognosis.remaining_life(conn, fid, until=until)
    except CalcError:
        return {}
    w, f = r["window"], r["first"]
    out = {"next_window": f"{w['name']} from {w['start']}" if w else None}
    if f:
        rng = f" (range {f['range'][0]} to {f['range'][1]})" if f.get("range") and f["range"][0] else ""
        out["remaining_life"] = (f"{f['what']}: now" if f["reaches"] == "now" else f"{f['what']} by {f['reaches']}{rng}")
        out["makes_the_window"] = f["verdict"]
    return out


def watch(conn, until: str | None = None, by: str = "agent:rotating_screen", watch_level: bool = True) -> dict:
    """The fleet screen at `until` raised as `machine_health` exceptions: one per machine whose condition is flagged
    (failure-history flags stay with the reliability review), kept while the screen flags it (new evidence updates
    the open one), cleared when it doesn't. `act` machines come at priority 2, `watch` ones at 0.5 (left out with
    `watch_level=False`). Each carries its status, the flags in words, the first
    recommendation and the findings behind them, so the engineer the case reaches starts from the evidence."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    s = screen(conn, until, limit=10 ** 6)
    held = {r["key"] for r in conn.execute("select key from exception_record where type = %s and state = 'open'",
                                           (HEALTH_TYPE,))}
    raised, cleared, errors, keep = [], [], [], set()
    for m in s["machines"]:
        flags = sorted((f for f in dict.fromkeys(m["flags"]) if f not in HISTORY),
                       key=lambda f: (f not in SEVERE, FIRST.index(f) if f in FIRST else len(FIRST)))
        status = "act" if any(f in SEVERE for f in flags) else "watch" if flags else "ok"
        if status == "ok" or (status == "watch" and not watch_level):
            continue
        m = {**m, "status": status}
        keep.add(m["machine"])
        words = [WORDS.get(f, f.replace("_", " ")) for f in flags]
        recs = [ACTIONS[f] for f in flags if f in ACTIONS]
        title = f"{m['machine']} {'needs action' if m['status'] == 'act' else 'to watch'}: " + ", ".join(words)
        evidence = {"status": m["status"], "flags": ", ".join(flags), "recommendation": (recs or [None])[0],
                    "recommendations": recs, "at": s["at"], "not_checked": ", ".join(m["unknown"]) or None,
                    "basis": s["basis"], **_prognosis(conn, m["id"], until)}
        try:
            r = raise_exception(conn, HEALTH_TYPE, m["machine"], title, by, source="rotating_screen", refs=[m["id"]],
                                priority=PRIORITY[m["status"]], evidence=evidence)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{m['machine']}: {e}")
            continue
        raised.append({"machine": m["machine"], "status": m["status"], "exception_id": r["id"], "item_id": r["item_id"],
                       "new": r["raised"]})
    for key in held - keep:
        clear_exception(conn, HEALTH_TYPE, key, by, reason="the fleet screen no longer flags it")
        cleared.append(key)
    conn.commit()
    return {"at": until, "checked": s["machines_checked"], "raised": raised, "cleared": cleared, "errors": errors}
