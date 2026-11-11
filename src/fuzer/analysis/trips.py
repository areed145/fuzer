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
- `scan(since, until)`: every compressor train's trips in the window, diagnosed, each raised as a `machine_trip`
  exception for the IOC. It joins the case already open on the train (the upset the stop raised), so the IOC works
  one case with the cause and the restart checklist on it, and the operator dispatched to the train sees the
  checklist on their field job.
"""

import re
from datetime import datetime, timedelta

import numpy as np

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


def _family(desc: str, unit_label: str, direction: int) -> str:
    """The cause family a point stands for, from what it measures and what it is on."""
    d = (desc or "").lower()
    if "vibration" in d:
        return "vibration"
    if "bearing" in d:
        return "bearing"
    if "anti-surge" in d or "suction flow" in d:
        return "surge"
    if re.search(r"-(GT|EG|EM|DR)-", unit_label) or any(w in d for w in ("speed", "exhaust temperature", "fuel",
                                                                          "output power")):
        return "driver"
    if "level" in d:
        return "liquid_carryover" if direction > 0 else "process"
    if "cylinder" in d or ("discharge temperature" in d and "-K-" in unit_label):
        return "discharge_temperature"
    if "temperature" in d and "-E-" in unit_label:
        return "cooling" if direction > 0 else "process"
    if "suction pressure" in d or ("pressure" in d and "-V-" in unit_label):
        return "suction_pressure"
    if "discharge pressure" in d or "interstage" in d:
        return "discharge_pressure" if direction > 0 else "process"
    return "process"


# ---- the train ------------------------------------------------------------------------------------------------

def _tag(conn, fid: str) -> str:
    from .rotating import _tag as tag

    return tag(conn, fid)


def train(conn, fid: str) -> dict:
    """The train a machine belongs to: tags in its area with its trailing train letter (20-K-2200B: area 20, train
    B), or every tag in its area when it has none (Kestrel's B1 area is one train); plus the vessels in a lettered
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
    shared = [r for r in rows if letter and not lettered(r["label"]) and "-V-" in r["label"]]
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
    from ..sources.timeseries import _unit_points

    out = {}
    for u in units:
        ul = _label(conn, u)
        for p in _unit_points(conn, u, 30):
            out[p["id"]] = {**p, "unit": u, "unit_label": ul}
    return out


def _run_signal(conn, pack, tr: dict, pts: dict[str, dict]) -> dict:
    """What says the train runs: its driver's speed, else its power, else the pack's run signal for the machine. A
    vibration run signal (some packs use one) would hide the vibration that tripped it, so the driver comes first."""
    from . import limits

    rules = {r["uom"]: r for r in limits.config(pack)["run_signals"] if "uom" in r}
    for uom in ("rpm", "kW"):
        p = next((p for p in sorted(pts.values(), key=lambda p: p["point"]) if p["uom"] == uom), None)
        if p:
            on = float(rules.get(uom, {"above": 1000 if uom == "rpm" else 20})["above"])
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
    stop = found[-1]
    pts = _points(conn, tr["members"] + tr["shared"])
    sig = _run_signal(conn, pack, tr, pts)
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
                              "family": _family(p["description"], p["unit_label"], d)})
                break
        # at its trip setting in the quarter hour before (not after: a falling flow is the trip, not its cause)
        last = [x for t, x in v.items() if (t0 - timedelta(minutes=15)).isoformat(timespec="minutes") <= t < t0.isoformat(timespec="minutes")]
        lim = lims.get(pid, {})
        if last and "hihi" in lim and max(last) >= lim["hihi"]:
            at_trip.append({"point": p["point"], "value": round(float(max(last)), 2), "trip_setting": lim["hihi"],
                            "side": "high", "unit": p["unit_label"]})
        elif last and "lolo" in lim and min(last) <= lim["lolo"]:
            at_trip.append({"point": p["point"], "value": round(float(min(last)), 2), "trip_setting": lim["lolo"],
                            "side": "low", "unit": p["unit_label"]})
    moved.sort(key=lambda m: (m["at"], m["point"]))
    first = moved[0] if moved else None
    family = first["family"] if first else "none"
    if first and family == "process":  # a machine signal close behind a process one is the likelier cause
        mech = next((m for m in moved if m["family"] in ("vibration", "bearing", "discharge_temperature")
                     and m["lead_minutes"] >= first["lead_minutes"] - 30), None)
        family = mech["family"] if mech else family
    fam = FAMILIES[family]
    prior = _prior(conn, pack, fid, t0, src) if history else []
    same = [p for p in prior if p["family"] == family and p["days_before"] <= REPEAT_DAYS]
    verdict = "inspect_first" if same else "review" if prior else "restart_after_checks"
    checks = list(fam["checks"])
    if same:
        checks.insert(0, f"Second {fam['label']} trip in {same[0]['days_before']:g} days ({same[0]['at'][:10]}): "
                         f"inspect the machine before another restart.")
    elif prior:
        checks.append(f"Trip {len(prior) + 1} on this train in {REVIEW_DAYS} days (last {prior[0]['at'][:10]}, "
                      f"{FAMILIES[prior[0]['family']]['label']}): ask reliability to review it.")
    culprit = first["unit"] if first else None
    name = tr["name"][0].upper() + tr["name"][1:]
    title = (f"{name} tripped {t0:%Y-%m-%d %H:%M}: "
             + (f"{fam['label']} on {culprit}, {first['point']} moved {first['lead_minutes']} min before"
                if first else "no precursor in the data")
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
            "tripped_on": at_trip, "verdict": verdict, "restart": VERDICTS[verdict], "checklist": checks,
            "prior_trips": prior, "machines": tr["machines"], "title": title,
            "culprit_id": first["unit_id"] if first else None}


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
        for t in found:
            if planned(conn, t):
                continue
            try:
                d = diagnose(conn, m, t.isoformat(timespec="minutes"), pack=pack, src=src)
            except CalcError as e:
                out["errors"].append(str(e))
                continue
            out["trips"] += 1
            evidence = {"cause": d["cause"], "means": d["means"], "restart": d["restart"], "verdict": d["verdict"],
                        "tripped_at": d["tripped_at"], "train": d["train"],
                        "first_out": d["first_out"]["point"] if d["first_out"] else "none in the data",
                        "first_out_minutes_before": d["first_out"]["lead_minutes"] if d["first_out"] else None,
                        "tripped_on": ", ".join(f"{x['point']} {x['value']:g} (trip {x['trip_setting']:g})"
                                                for x in d["tripped_on"]) or None,
                        "earlier_trips": len(d["prior_trips"]), "checklist": d["checklist"],
                        "sequence": d["sequence"][:6], "basis": d["basis"]}
            try:
                res = raise_exception(conn, TYPE, f"{d['train_key']}@{d['tripped_at']}", d["title"], by,
                                      source="trips", refs=[x for x in [d["culprit_id"], *d["machines"]] if x],
                                      priority=2.0 if d["verdict"] == "inspect_first" else 1.0, evidence=evidence)
            except (work.WorkError, ExceptionError) as e:
                out["errors"].append(str(e))
                continue
            out["raised"].append({"exception_id": res["id"], "item_id": res["item_id"], "title": d["title"],
                                  "new": res["raised"]})
    conn.commit()
    return out
