"""Compressor trip diagnosis: why a compression train stopped, and what to check before it restarts.

When a train trips, the control room and the operator sent to it need two answers fast: what tripped it, and whether
it is safe to start again. The DCS first-out panel says which trip fired; the data says what moved first and how
long before, which is what separates a machine fault from a process upset and from a bad transmitter.

- `diagnose(ref, at)`: the train the machine belongs to (the machines, scrubbers, coolers and driver of one train,
  and the inlet vessels every train shares), the moment it tripped (its run signal falling), and the sequence of
  events before it: each point's departure from its own last day (a robust z-score, sustained), earliest first. The
  first to move is the first-out; its kind of signal gives the cause family (vibration, discharge temperature,
  liquid carryover, surge, driver, suction pressure, ...), an ISO 13379-1 symptom-to-cause reading. Points at their
  trip setting (hihi / lolo) when it stopped are `tripped_on`. Nothing moving before the stop means an ESD, a power
  dip or a spurious trip: no precursor, so check the first-out panel and the transmitter before blaming the machine.
- The restart gate: each family carries the checks to make before a restart (OEM and site practice, API 670 for the
  protection channels). A second trip of the same family on the train within `REPEAT_DAYS` is `inspect_first`: an
  inspection, not another restart (the site procedure for repeated vibration trips). Any other trip in the last
  `REVIEW_DAYS` asks for a reliability review.
- Spurious trips: when nothing moved ahead of the stop and the one point at its trip setting got there in a single
  step from its normal value, with no other point on the train following it, the trip is called `spurious`: a failed
  transmitter, a loose probe or its wiring, not the machine (ISO 14224's "spurious operation" of the protection).
  It doesn't count as a repeat of a real fault in the restart gate.
- `starts(ref, days)`: start attempts that failed: the run signal rose but fell back before it held running speed
  for half an hour. Each with the reason the data gives: the point past its trip setting at the attempt's peak (low
  lube oil pressure, high vibration, ...), or that it never got near running speed (ignition, fuel, starter or a
  start permissive). A failed start after a trip shows on the trip's diagnosis as `restart_attempts`.
- `scan(since, until)`: every compressor train's trips in the window, diagnosed, each raised as a `machine_trip`
  exception for the IOC. It joins the case already open on the train (the upset the stop raised), so the IOC works
  one case with the cause and the restart checklist on it, and the operator dispatched to the train sees the
  checklist on their field job.
"""

import re
from datetime import datetime, timedelta

import numpy as np

from ..core import detectors
from ..core import roles as vocabulary
from ..core.findings import DETERMINISTIC, Finding, asset, at, fact
from ..graph import roles
from .calcs import CalcError, _label, read

STEP = 5  # minutes: the grid the sequence of events is read on
LOOKBACK_HOURS = 6  # the window before a trip a precursor is looked for in
BASELINE_HOURS = 24  # the stretch before that the points' normal is taken from
Z = 4.0  # robust z-score a point must pass to have moved
SUSTAIN = 2  # steps in a row past it
REPEAT_DAYS = 30  # a second trip of the same family on the train within this: inspect, don't restart
REVIEW_DAYS = 45  # any earlier trip within this: ask reliability to review
CO = r"^ISO14224:CO([.]|$)"

# Cause families: what a first-out of that kind usually means, and the checks before a restart.
FAMILIES = {
    "vibration": {
        "label": "high vibration",
        "means": "a mechanical fault on the machine: bearing wear, misalignment, looseness or a rub",
        "checks": ["Read the vibration and bearing temperature trend for the hours before the trip, not only the "
                   "value at the trip.",
                   "Check the protection channels (API 670): shaft vibration, axial thrust position and bearing pad "
                   "temperatures, if fitted.",
                   "Walk down the machine: coupling guard, foundation bolts, lube oil pressure and temperature, oil "
                   "level and colour.",
                   "On restart, hold at minimum load and compare vibration with its value before the trouble began; "
                   "stop if it comes back higher."]},
    "bearing": {
        "label": "high bearing temperature",
        "means": "a bearing running hot: lube oil supply, wear or thrust overload",
        "checks": ["Check lube oil supply pressure, temperature and filter differential.",
                   "Check thrust position and the thrust pad temperatures against their alarm settings.",
                   "Take an oil sample for water and particles before restarting."]},
    "discharge_temperature": {
        "label": "high discharge temperature",
        "means": "hot gas: a fouled cooler before the stage, recycle through a hot cooler, or a high ratio",
        "checks": ["Check the after-cooler before this stage: outlet temperature against its clean baseline, fans "
                   "or cooling water.",
                   "Check the anti-surge (recycle) valve: open recycle heats the suction.",
                   "Restart only when the cooler is fixed or the load is cut; the trip will repeat on the next hot "
                   "afternoon otherwise."]},
    "cooling": {
        "label": "loss of cooling",
        "means": "a cooler that stopped rejecting heat: fouling, a fan or the cooling water",
        "checks": ["Check the cooler's outlet temperature trend and its fans or cooling water supply.",
                   "Restart at reduced load until the outlet temperature is back near its clean baseline."]},
    "liquid_carryover": {
        "label": "liquid carryover",
        "means": "liquid reaching the suction: a scrubber or the inlet separator running high",
        "checks": ["Drain the suction scrubber and confirm its level transmitter against the gauge glass.",
                   "Check the inlet separator's level and its level control valve.",
                   "Liquid in the suction erodes and unbalances the impellers: check vibration closely on restart."]},
    "surge": {
        "label": "surge",
        "means": "forward flow falling to the surge line: low throughput, a slow anti-surge valve or a blocked outlet",
        "checks": ["Check the anti-surge valve's stroke time and the controller's response before restarting.",
                   "After a surge, check thrust position, vibration and the dry gas seals (a surge reverses the "
                   "thrust in a fraction of a second).",
                   "Find why throughput fell: load sharing between trains, an inlet cut, a closed outlet valve."]},
    "driver": {
        "label": "driver trip",
        "means": "the gas turbine, engine or motor stopped first: fuel, flame, overspeed, its own protection",
        "checks": ["Read the driver's own first-out (flame, overspeed, exhaust temperature spread, fuel pressure).",
                   "Check the fuel gas supply pressure and filters.",
                   "The compressor followed the driver: no compressor inspection needed unless it surged on the way "
                   "down."]},
    "suction_pressure": {
        "label": "suction pressure",
        "means": "the gas supply to the train changed: an inlet cut, a scrubber or upstream upset",
        "checks": ["Check the inlet: separator pressure and level, upstream valves, the wells or pipeline feeding it.",
                   "Restart when the suction pressure is steady inside its normal range."]},
    "discharge_pressure": {
        "label": "high discharge pressure",
        "means": "the outlet was restricted: a closed valve downstream or the export or header backing up",
        "checks": ["Check the outlet line-up and the export or header pressure.",
                   "Restart when the downstream pressure is back in its normal range."]},
    "process": {
        "label": "a process change",
        "means": "a process value moved first, with no machine signal ahead of it",
        "checks": ["Check what moved first and why before restarting."]},
    "spurious": {
        "label": "a spurious trip",
        "means": "one transmitter jumped past its trip setting in a single step while nothing else on the train moved: "
                 "a failed transmitter, a loose probe or its wiring, not the machine",
        "checks": ["Check the transmitter that tripped it: its loop, cable and junction box, and the probe's gap "
                   "voltage for a proximity probe.",
                   "Compare it with its neighbours (the other bearing, shaft against casing vibration, the redundant "
                   "transmitter): a real fault shows on more than one.",
                   "Walk the machine down as for any trip, then restart once the instrument is fixed or confirmed.",
                   "Log it as an instrument failure (spurious operation), so it isn't counted against the machine."]},
    "none": {
        "label": "no precursor",
        "means": "nothing moved before the stop: an ESD, a power dip, a spurious trip or a stop by hand",
        "checks": ["Read the DCS first-out panel and the operator log for the trip that fired.",
                   "If the first-out names one transmitter, check it against its neighbours before blaming the "
                   "machine: a failed transmitter trips a healthy train.",
                   "Restart after the standard pre-start checks once the cause is known."]},
}
VERDICTS = {"inspect_first": "Inspect before restarting",
            "review": "Restart after checks; reliability to review",
            "restart_after_checks": "Restart after checks"}


DRIVERS = {"GT", "ST", "TE", "CE", "EM", "EG"}  # ISO 14224 drivers: gas and steam turbines, engines, motors
_DRIVER_ROLES = {"speed", "power", "exhaust_temperature", "fuel_flow"}


def _family(role: str | None, classes, direction: int) -> str:
    """The cause family a point stands for, from its measurement role and the class of what it is on."""
    classes = set(classes or ())
    quantity = vocabulary.ROLES[role].quantity if role in vocabulary.ROLES else None
    if role in ("vibration", "shaft_vibration"):
        return "vibration"
    if role == "bearing_temperature":
        return "bearing"
    if role in ("anti_surge_valve_position", "suction_flow"):
        return "surge"
    if classes & DRIVERS or role in _DRIVER_ROLES:
        return "driver"
    if role == "level":
        return "liquid_carryover" if direction > 0 else "process"
    if role == "cylinder_discharge_temperature" or (role == "discharge_temperature" and "CO" in classes):
        return "discharge_temperature"
    if quantity == "temperature" and "HE" in classes:
        return "cooling" if direction > 0 else "process"
    if role == "suction_pressure" or (quantity == "pressure" and "VE" in classes):
        return "suction_pressure"
    if role in ("discharge_pressure", "interstage_pressure"):
        return "discharge_pressure" if direction > 0 else "process"
    return "process"


# ---- the train ------------------------------------------------------------------------------------------------

def _tag(conn, fid: str) -> str:
    from .rotating import _tag as tag

    return tag(conn, fid)


def train(conn, fid: str) -> dict:
    """The train a machine belongs to: tags in its area with its trailing train letter (20-K-2200B: area 20, train
    B), or every tag in its area when it has none (an area such as B1 that is one train); plus the vessels in a lettered
    area that belong to no train (the inlet separation every train shares)."""
    tag = _tag(conn, fid)
    label = _label(conn, tag)
    area = conn.execute("select a.dst from edge a join entity x on x.fuzer_id = a.dst and x.kind = 'area' "
                        "where a.src = %s and a.rel = 'part_of' limit 1", (tag,)).fetchone()
    if not area:
        raise CalcError(f"{label} is in no area, so its train can't be told")
    rows = conn.execute(
        """
        select l.fuzer_id, l.label from edge a join entity_label l on l.fuzer_id = a.src
        join entity e on e.fuzer_id = a.src and e.kind = 'tag'
        where a.dst = %s and a.rel = 'part_of'
          and not exists (select 1 from edge p join entity x on x.fuzer_id = p.dst and x.kind = 'tag'
                          where p.src = a.src and p.rel = 'part_of')
        """, (area["dst"],)).fetchall()
    letter = m.group(1) if (m := re.search(r"\d([A-Z])$", label)) else ""
    lettered = lambda s: (re.search(r"\d([A-Z])$", s) or [None, ""])[1]
    members = [r for r in rows if lettered(r["label"]) == letter] if letter else rows
    shared = [r for r in rows if letter and not lettered(r["label"]) and "VE" in roles.classes(conn, r["fuzer_id"])]
    machines = [r["fuzer_id"] for r in members if conn.execute(
        """
        select 1 from edge i join edge c on c.src = i.src and c.rel = 'classified_as'
        join entity_label cl on cl.fuzer_id = c.dst
        where i.dst = %s and i.rel = 'installed_at' and i.valid_to is null and cl.label ~ %s limit 1
        """, (r["fuzer_id"], CO)).fetchone()]
    area_label = _label(conn, area["dst"])
    return {"name": f"train {letter} ({area_label})" if letter else f"train {area_label}",
            "key": f"{area_label}:{letter}" if letter else area_label, "area": area["dst"],
            "members": [r["fuzer_id"] for r in members], "shared": [r["fuzer_id"] for r in shared],
            "machines": machines or [tag]}


def _points(conn, units: list[str]) -> dict[str, dict]:
    """The points on the train's units, each with its measurement role and the class codes of its unit."""
    from ..sources.timeseries import _unit_points

    out = {}
    for u in units:
        ul, cls = _label(conn, u), sorted(roles.classes(conn, u))
        for p in _unit_points(conn, u, 30):
            out[p["id"]] = {**p, "unit": u, "unit_label": ul, "classes": cls, "role": None}
    for pid, role in roles.of(conn, out).items():
        out[pid]["role"] = role
    return out


def _run_signal(conn, pack, tr: dict, pts: dict[str, dict]) -> dict:
    """What says the train runs: its driver's speed, else its power, else the pack's run signal for the machine. A
    vibration run signal (some packs use one) would hide the vibration that tripped it, so the driver comes first."""
    from . import limits

    rules = {r["uom"]: r for r in limits.config(pack)["run_signals"] if "uom" in r}
    for role, above in (("speed", 1000), ("power", 20)):  # the default running level (rpm, kW) when no rule says
        p = next((p for p in sorted(pts.values(), key=lambda p: p["point"]) if p["role"] == role), None)
        if p:
            on = float(rules.get(p["uom"], {"above": above})["above"])
            return {"point": p, "on": on, "off": on / 3}
    sig = limits.run_signals(conn, pack, set(tr["machines"]))
    for m in tr["machines"]:
        s = sig.get(m) or {}
        if "point" in s:
            return s
        for f in s.get("follow", []):
            if "point" in sig.get(f, {}):
                return sig[f]
    raise CalcError("no run signal for that train (no speed or power point on it or its driver)")


def stops(conn, pack, fid: str, since: datetime, until: datetime, src=None) -> list[datetime]:
    """When the machine's train stopped from running in the window (10-minute steps)."""
    from . import limits

    tr = train(conn, fid)
    m = limits.modes(conn, pack, {tr["machines"][0]}, since, until, 10, src)[tr["machines"][0]]
    running = (m == "running") | (m == "startup")
    out = []
    for i in range(1, len(m)):
        if m[i] == "stopped" and running[:i].any() and m[i - 1] != "stopped":
            out.append(since + timedelta(minutes=10 * i))
    return out


# ---- the sequence of events ------------------------------------------------------------------------------------

def _trip_time(series: dict[str, float], near: datetime, on: float) -> datetime:
    """The moment the run signal started to fall: walking back from the stop the modes saw, while it is below 97 %
    of where it ran before."""
    ts = sorted(series)
    before = [series[t] for t in ts if t < (near - timedelta(hours=1)).isoformat()]
    ran = float(np.median([v for v in before if v >= on])) if any(v >= on for v in before) else on
    idx = max((i for i, t in enumerate(ts) if t <= near.isoformat(timespec="minutes")), default=len(ts) - 1)
    i = idx
    while i > 0 and series[ts[i - 1]] < 0.97 * ran:
        i -= 1
    return datetime.fromisoformat(ts[i])


def _limits(conn, ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in conn.execute("select point_id, limit_type, min(value) as lo, max(value) as hi from point_limit "
                          "where point_id = any(%s) and limit_type in ('hihi', 'lolo') and enabled "
                          "group by point_id, limit_type", (ids,)):
        out.setdefault(r["point_id"], {})[r["limit_type"]] = r["lo"] if r["limit_type"] == "hihi" else r["hi"]
    return out


def diagnose(conn, fid: str, at: str | None = None, pack=None, src=None, history: bool = True) -> dict:
    """Why a machine's train tripped at (or last before) `at`, and the restart gate."""
    from ..core.pack import current_pack
    from .calcs import window_of

    pack = pack or current_pack(conn)
    tr = train(conn, fid)
    end = datetime.fromisoformat(at) if at else window_of(conn, 1)[1]
    found = stops(conn, pack, fid, end - timedelta(days=2), end + timedelta(hours=3), src)  # the run signal lags
    if not found:
        raise CalcError(f"{tr['name']} did not trip in the two days before {end:%Y-%m-%d %H:%M}")
    pts = _points(conn, tr["members"] + tr["shared"])
    sig = _run_signal(conn, pack, tr, pts)
    stop = next((t for t in reversed(found) if not _failed_start(conn, sig, t, src)), None)
    if stop is None:
        raise CalcError(f"{tr['name']} had only failed start attempts in the two days before {end:%Y-%m-%d %H:%M}")
    a = stop - timedelta(hours=LOOKBACK_HOURS + BASELINE_HOURS)
    b = stop + timedelta(minutes=30)
    run = read({"run": sig["point"]}, a, b, STEP, src)["run"]
    t0 = _trip_time(run, stop, sig["on"])
    pts.pop(sig["point"]["id"], None)  # the run signal falls because it stopped
    vals = read(pts, a, b, STEP, src)
    base_end, look = t0 - timedelta(hours=LOOKBACK_HOURS), t0 - timedelta(minutes=STEP)
    lims = _limits(conn, list(pts))
    moved, at_trip = [], []
    for pid, p in pts.items():
        v = vals.get(pid) or {}
        base = np.array([x for t, x in v.items() if t < base_end.isoformat()])
        if len(base) < 24:
            continue
        med = float(np.median(base))
        scale = max(1.4826 * float(np.median(np.abs(base - med))), 0.02 * abs(med), 0.05)
        win = sorted((t, x) for t, x in v.items() if base_end.isoformat() <= t <= look.isoformat(timespec="minutes"))
        z = [(t, (x - med) / scale) for t, x in win]
        for i in range(len(z) - SUSTAIN + 1):
            run_ = z[i:i + SUSTAIN]
            if all(abs(q) > Z for _, q in run_) and len({np.sign(q) for _, q in run_}) == 1:
                d = int(np.sign(run_[0][1]))
                moved.append({"point": p["point"], "id": pid, "unit": p["unit_label"], "unit_id": p["unit"],
                              "measures": p["description"], "at": run_[0][0][:16],
                              "lead_minutes": int((t0 - datetime.fromisoformat(run_[0][0])).total_seconds() // 60),
                              "direction": "up" if d > 0 else "down", "from": round(med, 2),
                              "to": round(float(win[-1][1]), 2), "unit_of_measure": p["uom"],
                              "family": _family(p["role"], p["classes"], d)})
                break
        # at its trip setting in the quarter hour up to the trip (not after: a falling flow is the trip, not its cause)
        last = [x for t, x in v.items() if (t0 - timedelta(minutes=15)).isoformat(timespec="minutes") <= t <= t0.isoformat(timespec="minutes")]
        lim = lims.get(pid, {})
        hit = None
        if last and "hihi" in lim and max(last) >= lim["hihi"]:
            hit = {"point": p["point"], "value": round(float(max(last)), 2), "trip_setting": lim["hihi"],
                   "side": "high", "unit": p["unit_label"]}
        elif last and "lolo" in lim and min(last) <= lim["lolo"]:
            hit = {"point": p["point"], "value": round(float(min(last)), 2), "trip_setting": lim["lolo"],
                   "side": "low", "unit": p["unit_label"]}
        if hit:
            # how it got there: the step from its normal to past the setting, in one reading or over several
            before = [x for t, x in sorted(v.items()) if t < (t0 - timedelta(minutes=15)).isoformat(timespec="minutes")]
            prev = before[-1] if before else None
            hit.update(id=pid, unit_id=p["unit"], measures=p["description"],
                       from_normal=prev is not None and abs(prev - med) <= max(Z * scale, 0.1 * abs(med)),
                       before=round(float(prev), 2) if prev is not None else None)
            at_trip.append(hit)
    moved.sort(key=lambda m: (m["at"], m["point"]))
    first = moved[0] if moved else None
    family = first["family"] if first else "none"
    spurious = None
    if not first and len(at_trip) == 1 and at_trip[0]["from_normal"]:
        spurious = at_trip[0]  # one transmitter, normal one reading and past its trip the next, nothing else moved
        family = "spurious"
    if first and family == "process":  # a machine signal close behind a process one is the likelier cause
        mech = next((m for m in moved if m["family"] in ("vibration", "bearing", "discharge_temperature")
                     and m["lead_minutes"] >= first["lead_minutes"] - 30), None)
        family = mech["family"] if mech else family
    fam = FAMILIES[family]
    prior = _prior(conn, pack, fid, t0, src) if history else []
    same = [p for p in prior if p["family"] == family and p["days_before"] <= REPEAT_DAYS and family != "spurious"]
    verdict = "inspect_first" if same else "review" if prior else "restart_after_checks"
    checks = list(fam["checks"])
    if same:
        checks.insert(0, f"Second {fam['label']} trip in {same[0]['days_before']:g} days ({same[0]['at'][:10]}): "
                         f"inspect the machine before another restart.")
    elif prior:
        checks.append(f"Trip {len(prior) + 1} on this train in {REVIEW_DAYS} days (last {prior[0]['at'][:10]}, "
                      f"{FAMILIES[prior[0]['family']]['label']}): ask reliability to review it.")
    attempts = [x for x in starts(conn, fid, until=(t0 + timedelta(hours=12)).isoformat(timespec="minutes"), days=0.5,
                                  pack=pack, src=src, sig=sig) if x["at"] > t0.isoformat(timespec="minutes")] if history else []
    if attempts:
        checks.append(f"{len(attempts)} restart attempt(s) failed after this trip, the first on "
                      f"{attempts[0]['reason']}: fix that before the next attempt.")
    culprit = first["unit"] if first else spurious["unit"] if spurious else None
    name = tr["name"][0].upper() + tr["name"][1:]
    title = (f"{name} tripped {t0:%Y-%m-%d %H:%M}: "
             + (f"{fam['label']} on {culprit}, {first['point']} moved {first['lead_minutes']} min before" if first
                else f"likely spurious, {spurious['point']} jumped past its trip setting with nothing else moving"
                if spurious else "no precursor in the data")
             + f". {VERDICTS[verdict]}")
    return {"value": family, "expected": None, "deviation": None, "deviation_pct": None, "flag": verdict if verdict != "restart_after_checks" else None,
            "basis": (f"the run signal {sig['point'].get('point') or sig['point'].get('label') or ''} fell at "
                      f"{t0:%Y-%m-%d %H:%M}; each of {len(pts)} points on the train judged against its own last "
                      f"{BASELINE_HOURS} h (robust z-score past {Z:g} for {SUSTAIN * STEP} min) over the "
                      f"{LOOKBACK_HOURS} h before; the first to move names the cause family (ISO 13379-1 "
                      "symptom-to-cause). Limits from the DCS and AF"),
            "sources": list(dict.fromkeys([sig["point"]["id"], *(m["id"] for m in moved[:8])])),
            "unit": "cause family", "train": tr["name"], "train_key": tr["key"], "tripped_at": t0.isoformat(timespec="minutes"),
            "cause": fam["label"], "means": fam["means"], "first_out": first, "sequence": moved[:12],
            "tripped_on": [{k: x[k] for k in ("point", "value", "trip_setting", "side", "unit")} for x in at_trip],
            "spurious": {k: spurious[k] for k in ("point", "before", "value", "trip_setting", "unit")} if spurious else None,
            "restart_attempts": attempts, "verdict": verdict, "restart": VERDICTS[verdict], "checklist": checks,
            "prior_trips": prior, "machines": tr["machines"], "title": title,
            "culprit_id": first["unit_id"] if first else spurious["unit_id"] if spurious else None}


HOLD_MINUTES = 30  # a start holds once the run signal stays near running for this long


def _ran_level(series: dict[str, float], on: float) -> float:
    vals = [x for x in series.values() if x >= on]
    return float(np.percentile(vals, 75)) if vals else on


def _failed_start(conn, sig: dict, stop: datetime, src=None) -> bool:
    """Whether the stop the modes saw ended a start attempt that never held running speed, rather than a run."""
    a = stop - timedelta(hours=3)
    run = read({"run": sig["point"]}, stop - timedelta(days=2), stop + timedelta(minutes=30), STEP, src)["run"]
    ran = _ran_level(run, sig["on"])
    ts = [t for t in sorted(run) if t >= a.isoformat()]
    # walk back from the stop to the last time it was below the off level: the attempt began there
    i = max((k for k, t in enumerate(ts) if t <= stop.isoformat(timespec="minutes")), default=len(ts) - 1)
    while i > 0 and run[ts[i]] < sig["off"]:
        i -= 1
    j = i
    while j > 0 and run[ts[j - 1]] >= sig["off"]:
        j -= 1
    if j == 0:
        return False  # running since before the window: a trip
    held = sum(1 for t in ts[j:i + 1] if run[t] >= 0.9 * ran)
    return held * STEP < HOLD_MINUTES


def starts(conn, fid: str, days: float = 30, until: str | None = None, pack=None, src=None, sig: dict | None = None) -> list[dict]:
    """Start attempts in the window that failed (the run signal rose and fell back before holding running speed for
    half an hour), each with the reason the data gives."""
    from ..core.pack import current_pack
    from .calcs import window_of

    pack = pack or current_pack(conn)
    tr = train(conn, fid)
    pts = _points(conn, tr["members"] + tr["shared"])
    sig = sig or _run_signal(conn, pack, tr, pts)
    start, end = window_of(conn, 1, until)
    start = end - timedelta(days=days)
    run = read({"run": sig["point"]}, start - timedelta(days=2), end, STEP, src)["run"]
    ran = _ran_level(run, sig["on"])
    ts = [t for t in sorted(run) if t >= start.isoformat()]
    out, i = [], 1
    while i < len(ts):
        if run[ts[i - 1]] < sig["off"] <= run[ts[i]]:  # it began to turn
            j = i
            while j < len(ts) and run[ts[j]] >= sig["off"]:
                j += 1
            seg = ts[i:j]
            held = sum(1 for t in seg if run[t] >= 0.9 * ran) * STEP
            if held < HOLD_MINUTES and j < len(ts):  # it fell back before holding
                peak = max(seg, key=lambda t: run[t])
                out.append({"at": ts[i][:16], "peak": round(run[peak], 1), "peak_at": peak[:16],
                            "peak_pct": round(run[peak] / ran * 100, 0), "minutes": len(seg) * STEP})
            i = j
        i += 1
    if not out:
        return out
    others = {k: p for k, p in pts.items() if k != sig["point"]["id"]}
    lims = _limits(conn, list(others))
    vals = read({k: others[k] for k in lims}, start, end, STEP, src) if lims else {}
    for x in out:
        hits = []
        for pid, lim in lims.items():
            val = vals.get(pid, {}).get(x["peak_at"])
            if val is None:
                continue
            if "lolo" in lim and val <= lim["lolo"]:
                hits.append((others[pid], val, lim["lolo"], "low"))
            elif "hihi" in lim and val >= lim["hihi"]:
                hits.append((others[pid], val, lim["hihi"], "high"))
        if hits:
            p, val, setting, side = hits[0]
            x.update(reason=f"{side} {p['description'].lower()} ({p['point']} {val:.3g}, "
                            f"trip {setting:g})", point=p["point"])
        elif x["peak_pct"] < 70:
            x.update(reason="it never got near running speed (ignition, fuel, the starter or a start permissive): "
                            "read the driver's start sequence", point=None)
        else:
            x.update(reason="it reached speed but did not hold: read the first-out panel for the attempt", point=None)
    return out


def failed_starts(conn, fid: str, days: int = 90, until: str | None = None) -> dict:
    """The calc: a train's failed start attempts in the window, with their reasons."""
    from .calcs import result

    tr = train(conn, fid)
    found = starts(conn, fid, days=days, until=until)
    reasons: dict[str, int] = {}
    for x in found:
        key = x["point"] or x["reason"].split(" (")[0]
        reasons[key] = reasons.get(key, 0) + 1
    return result(len(found), 0, "failed_starts" if found else None,
                  f"start attempts on {tr['name']} in the last {days} days: the driver's run signal rising past a "
                  f"third of its running level and falling back before holding 90 % of it for {HOLD_MINUTES} min; the "
                  "reason is the point past its trip setting at the attempt's peak, else how far it got",
                  [fid], unit="failed starts", train=tr["name"], attempts=found, by_reason=reasons)


def planned(conn, t: datetime, hours: float = 3.0) -> bool:
    """Whether a planned downtime record (a shutdown, planned maintenance) starts within `hours` of `t`: a stop by
    plan is not a trip."""
    return bool(conn.execute(
        """
        select 1 from entity en join golden s on s.fuzer_id = en.fuzer_id and s.field = 'start'
        where en.kind = 'downtime' and en.retired_at is null
          and case when (s.value #>> '{}') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' then left(s.value #>> '{}', 16)::timestamp end
              between %(a)s and %(b)s
          and exists (select 1 from golden r where r.fuzer_id = en.fuzer_id and r.field in ('reason', 'reason_category', 'reason_code')
                      and (r.value #>> '{}') ~* 'plan')
        limit 1
        """, {"a": t - timedelta(hours=hours), "b": t + timedelta(hours=hours)}).fetchone())


def _prior(conn, pack, fid: str, t0: datetime, src=None) -> list[dict]:
    """The train's earlier trips within REVIEW_DAYS, newest first, each with its cause family (planned stops left
    out)."""
    earlier = stops(conn, pack, fid, t0 - timedelta(days=REVIEW_DAYS), t0 - timedelta(hours=2), src)
    out = []
    for t in reversed(earlier):
        if planned(conn, t):
            continue
        try:
            d = diagnose(conn, fid, t.isoformat(timespec="minutes"), pack=pack, src=src, history=False)
        except CalcError:
            continue
        when = datetime.fromisoformat(d["tripped_at"])
        if out and abs((datetime.fromisoformat(out[-1]["at"]) - when).total_seconds()) < 3600:
            continue  # the same trip seen twice (a failed restart)
        out.append({"at": d["tripped_at"], "family": d["value"], "days_before": round((t0 - when).total_seconds() / 86400, 1)})
    return out


# ---- raising them --------------------------------------------------------------------------------------------

def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("machine_trip") or exception_type(
        "machine_trip", "Compressor trip", role="role:engineer", audience=("ioc", "field"),
        description="A compression train tripped: the likely cause from what moved first, and the checks before "
                    "it restarts.")


TYPE = _type().name


def compressor_trains(conn) -> list[str]:
    """One machine per compression train (the first compressor of each), so each train is diagnosed once."""
    rows = conn.execute(
        """
        select distinct i.dst as tag, l.label from edge i
        join edge c on c.src = i.src and c.rel = 'classified_as'
        join entity_label cl on cl.fuzer_id = c.dst
        join entity_label l on l.fuzer_id = i.dst
        where i.rel = 'installed_at' and i.valid_to is null and cl.label ~ %s order by l.label
        """, (CO,)).fetchall()
    seen, out = set(), []
    for r in rows:
        try:
            key = train(conn, r["tag"])["key"]
        except CalcError:
            continue
        if key not in seen:
            seen.add(key)
            out.append(r["tag"])
    return out


@detectors.register("compressor_trips", title="A compression train tripping, with its first cause", kind=detectors.RULE,
                    cadence="6h", raises=("machine_trip",), classes=("compressor",),
                    roles=("running_status", "vibration", "bearing_temperature", "discharge_temperature"),
                    needs={"historian points": detectors.POINTS,
                           "a compression train": lambda conn, pack: bool(compressor_trains(conn))},
                    eval="test:tests/analysis/test_trips.py::test_trips_reach_the_ioc_with_the_restart_checklist",
                    found=lambda out: len(out["raised"]))
def scan(conn, pack, since: str, until: str, src=None, by: str = "agent:sre") -> dict:
    """Diagnose every compression train's trips in the window and raise each as a `machine_trip` exception."""
    from ..core import work
    from ..core.exceptions import ExceptionError, raise_exception

    a, b = datetime.fromisoformat(since), datetime.fromisoformat(until)
    out = {"trains": 0, "trips": 0, "raised": [], "errors": []}
    for m in compressor_trains(conn):
        out["trains"] += 1
        try:
            found = stops(conn, pack, m, a, b, src)
        except CalcError:
            continue
        sig = None
        for t in found:
            if planned(conn, t):
                continue
            try:
                if sig is None:
                    tr = train(conn, m)
                    sig = _run_signal(conn, pack, tr, _points(conn, tr["members"] + tr["shared"]))
                if _failed_start(conn, sig, t, src):
                    continue  # a failed restart shows on the trip it followed
                d = diagnose(conn, m, t.isoformat(timespec="minutes"), pack=pack, src=src)
            except CalcError as e:
                out["errors"].append(str(e))
                continue
            out["trips"] += 1
            sp = d["spurious"]
            found = Finding(
                "trips.diagnosis", DETERMINISTIC, method="compressor_trip",
                start=d["tripped_at"], checks=d["checklist"],
                data={"verdict": d["verdict"], "tripped_at": d["tripped_at"], "sequence": d["sequence"][:6]},
                items=(at("Tripped", d["tripped_at"]), fact("Train", d["train"]), asset("Likely source", d["culprit_id"]),
                       fact("Likely cause", d["cause"]), fact("What it means", d["means"]),
                       fact("Before restart", d["restart"]),
                       fact("First out", d["first_out"]["point"] if d["first_out"] else "none in the data"),
                       fact("First out led the trip by", d["first_out"]["lead_minutes"] if d["first_out"] else None, "min"),
                       fact("Tripped on", ", ".join(f"{x['point']} {x['value']:g} (trip {x['trip_setting']:g})"
                                                    for x in d["tripped_on"])),
                       fact("Earlier trips", len(d["prior_trips"])),
                       fact("Looks spurious", f"{sp['point']} jumped from {sp['before']:g} to {sp['value']:g} "
                                              f"(trip {sp['trip_setting']:g})" if sp else None),
                       fact("Failed restarts", "; ".join(f"{x['at']}: {x['reason']}" for x in d["restart_attempts"]))))
            try:
                res = raise_exception(conn, TYPE, f"{d['train_key']}@{d['tripped_at']}", d["title"], by,
                                      source="trips", refs=[x for x in [d["culprit_id"], *d["machines"]] if x],
                                      priority=2.0 if d["verdict"] == "inspect_first" else 1.0, finding=found)
            except (work.WorkError, ExceptionError) as e:
                out["errors"].append(str(e))
                continue
            out["raised"].append({"exception_id": res["id"], "item_id": res["item_id"], "title": d["title"],
                                  "new": res["raised"]})
    conn.commit()
    return out
