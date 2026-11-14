"""Flare event attribution (P3.1e, #351): a flare above its own baseline, matched to the records around it (downtime,
the operator log, other detections at the time), so the flaring report starts from evidence rather than a blank. Read
from flare flow points by measurement role (`flare_flow`) and from the daily reports' flare volume, so nothing here
knows a field, a flare system or a permit.

- `events(conn, until, days)`: each flare episode in the window.
  - On a flare flow point: a run of at least `MIN_STEPS` readings above `SPIKE` times the point's own baseline (its
    median over the window, the purge level), with its peak and the volume over the baseline.
  - On the daily reports: a day whose flare is `DAY_SPIKE` times the median of the `DAY_BASE` days before it, unless a
    flare flow point already shows an episode that day (the same flaring, read finer).
- `attribute(conn, start, end)`: the downtime events overlapping the episode, the log entries in it that speak of
  flaring, and other detections that started in it. An episode with none is reported with its cause unknown.
- `relief(conn, e)`: where the gas came from (API 521). The relief and vent paths into the flare system are read from
  the piping: each valve or line piped into the flare's area from another, and the item it relieves. An item whose
  pressure rose by `RISE` or more over the hours before the episode is named as a likely source, with its devices. It
  says which equipment was relieving, not why: the records above are the why (P4.2e, #850).
- The `flare_events` detector raises each episode as `flare_event` for the HSE specialist, with the records it was
  matched to as evidence. It is evidence only: not an emissions inventory, and not a compliance finding.
"""

import re
from datetime import datetime, timedelta
from statistics import median

from ..core import clock, detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact

SPIKE = 3.0  # times the point's baseline
MIN_STEPS = 2  # readings above it that make an episode
STEP_MINUTES = 10
DAY_SPIKE = 1.5  # a day's flare over the median of the days before it
DAY_BASE = 30
PAD = timedelta(hours=1)  # records this close to an episode's ends belong to it
DAYS = 3
RISE = 0.05  # a relieved item's pressure up this share over the hours before an episode
BEFORE = timedelta(hours=6)
RELIEF_ROLES = ("pressure", "discharge_pressure")  # the pressure a relief or vent path off an item answers to
FLARING = re.compile(r"\bflar(e|ed|es|ing)\b", re.IGNORECASE)


def _points(conn) -> list[dict]:
    return conn.execute(
        """
        select r.point_id, r.asset, coalesce(l.label, r.asset) as label
        from measurement_role r
        left join lateral (select label from entity_label where fuzer_id = r.asset order by label limit 1) l on true
        where r.role = 'flare_flow' order by r.point_id
        """).fetchall()


def _runs(series: list[tuple[str, float | None]], over: float) -> list[list[tuple[str, float]]]:
    runs, cur = [], []
    for t, v in series:
        if v is not None and v > over:
            cur.append((t, v))
        else:
            if len(cur) >= MIN_STEPS:
                runs.append(cur)
            cur = []
    if len(cur) >= MIN_STEPS:
        runs.append(cur)
    return runs


def _from_points(conn, start: datetime, end: datetime, src) -> list[dict]:
    from ..sources import timeseries
    from .calcs import read

    rows = _points(conn)
    pts = {r["point_id"]: p for r in rows if (p := timeseries._point(conn, r["point_id"]))}
    if not pts:
        return []
    src = src or timeseries.provider()
    vals = read(pts, start, end, STEP_MINUTES, src)
    out = []
    for r in rows:
        if r["point_id"] not in pts:
            continue
        series = sorted(vals[r["point_id"]].items())
        known = [v for _, v in series if v is not None]
        base = median(known) if known else None
        if not base or base <= 0:
            continue
        for run in _runs(series, SPIKE * base):
            per_day = sum(v - base for _, v in run) * STEP_MINUTES / 1440  # the point's unit per day, times days
            uom = pts[r["point_id"]].get("uom")
            out.append({"source": "point", "subject": r["asset"], "label": r["label"],
                        "point": pts[r["point_id"]]["point"], "start": run[0][0][:16],
                        "end": (datetime.fromisoformat(run[-1][0][:16]) + timedelta(minutes=STEP_MINUTES))
                        .isoformat(timespec="minutes"), "peak": round(max(v for _, v in run), 1),
                        "baseline": round(base, 2), "uom": uom, "volume": round(per_day, 1),
                        "volume_unit": re.sub(r"/d(ay)?$", "", uom) if uom and re.search(r"/d(ay)?$", uom) else None,
                        "provider": getattr(src, "name", None)})
    return out


def _from_reports(conn, start: datetime, end: datetime) -> list[dict]:
    days = conn.execute(
        """
        select e.fuzer_id, coalesce(l.label, e.fuzer_id) as label, g.value #>> '{}' as day,
               (select f.value #>> '{}' from golden f where f.fuzer_id = e.fuzer_id and f.field = 'flare_ksm3') as flare
        from entity e join golden g on g.fuzer_id = e.fuzer_id and g.field = 'report_date'
        left join lateral (select label from entity_label where fuzer_id = e.fuzer_id order by label limit 1) l on true
        where e.kind = 'production_day' and e.retired_at is null and e.merged_into is null
          and g.value #>> '{}' >= %s and g.value #>> '{}' < %s
        order by 3
        """, ((start - timedelta(days=DAY_BASE)).date().isoformat(), end.date().isoformat())).fetchall()
    known = [(d["day"][:10], float(d["flare"]), d) for d in days if d["flare"] not in (None, "")]
    out = []
    for i, (day, v, d) in enumerate(known):
        before = [x for _, x, _ in known[max(0, i - DAY_BASE):i]]
        if day < start.date().isoformat() or len(before) < DAY_BASE // 2:
            continue
        base = median(before)
        if base > 0 and v >= DAY_SPIKE * base:
            nxt = (datetime.fromisoformat(day) + timedelta(days=1)).isoformat(timespec="minutes")
            out.append({"source": "daily_report", "subject": d["fuzer_id"], "label": d["label"], "point": None,
                        "start": f"{day}T00:00", "end": nxt, "peak": v, "baseline": round(base, 1), "uom": "kSm3/d",
                        "volume": round(v - base, 1), "volume_unit": "kSm3", "provider": None})
    return out


def attribute(conn, start: str, end: str, skip: str | None = None) -> list[dict]:
    """The records around an episode: downtime overlapping it, log entries in it that speak of flaring, and other
    detections that started in it (`skip`: the flare's own type)."""
    a, b = datetime.fromisoformat(start) - PAD, datetime.fromisoformat(end) + PAD
    lo, hi = a.isoformat(timespec="minutes"), b.isoformat(timespec="minutes")
    got = []
    for r in conn.execute(
            """
            select e.fuzer_id, coalesce(l.label, e.fuzer_id) as label, s.value #>> '{}' as start,
                   (select f.value #>> '{}' from golden f where f.fuzer_id = e.fuzer_id and f.field = 'end') as end_,
                   (select f.value #>> '{}' from golden f where f.fuzer_id = e.fuzer_id and f.field = 'comment') as said,
                   (select f.value #>> '{}' from golden f where f.fuzer_id = e.fuzer_id and f.field = 'reason_code')
                       as reason
            from entity e join golden s on s.fuzer_id = e.fuzer_id and s.field = 'start'
            left join lateral (select label from entity_label where fuzer_id = e.fuzer_id order by label limit 1) l
                on true
            where e.kind = 'downtime' and e.retired_at is null and e.merged_into is null and s.value #>> '{}' < %s
            order by 3
            """, (hi,)):
        if (r["end_"] or r["start"]) >= lo:
            got.append({"kind": "downtime", "id": r["fuzer_id"], "at": r["start"][:16],
                        "text": " ".join(x for x in (r["reason"], r["said"]) if x)})
    for r in conn.execute(
            """
            select e.fuzer_id, t.value #>> '{}' as at,
                   (select f.value #>> '{}' from golden f where f.fuzer_id = e.fuzer_id and f.field = 'text') as said
            from entity e join golden t on t.fuzer_id = e.fuzer_id and t.field = 'timestamp'
            where e.kind = 'log_entry' and e.retired_at is null and e.merged_into is null
              and t.value #>> '{}' >= %s and t.value #>> '{}' < %s
            order by 2
            """, (lo, hi)):
        if r["said"] and FLARING.search(r["said"]):
            got.append({"kind": "log_entry", "id": r["fuzer_id"], "at": r["at"][:16], "text": r["said"]})
    for r in conn.execute(
            """
            select id, type, title, evidence #>> '{finding,start}' as at from exception_record
            where type <> coalesce(%s, '') and evidence #>> '{finding,start}' >= %s
              and evidence #>> '{finding,start}' < %s
            order by 4
            """, (skip, lo, hi)):
        got.append({"kind": "detection", "id": r["id"], "at": r["at"][:16], "text": f"{r['type']}: {r['title']}"})
    return got


def paths(conn, asset: str, plant=None) -> list[dict]:
    """The relief and vent paths into the flare system `asset` belongs to: each device piped into the flare's area
    from outside it, with the item it relieves (what is piped into the device, else what it is part of)."""
    from ..graph import structure

    p = plant or structure.load(conn)
    area = p.area.get(asset)
    pred: dict[str, set[str]] = {}
    for r in conn.execute("select src, dst from edge where rel = 'pipes_to' and valid_to is null"):
        pred.setdefault(r["dst"], set()).add(r["src"])
    part = {r["src"]: r["dst"] for r in conn.execute(
        "select src, dst from edge where rel in ('part_of', 'installed_at') and valid_to is null order by rel desc")}
    inside, todo, out = {asset}, [asset], []
    while todo:
        for x in sorted(pred.get(todo.pop(), ())):
            if x in inside:
                continue
            if area is not None and p.area.get(x) == area:
                inside.add(x)
                todo.append(x)
                continue
            inside.add(x)
            ups = sorted(pred.get(x, ())) or ([part[x]] if x in part else [])
            for u in ups:
                out.append({"device": x, "device_label": p.label(x), "relieves": u, "relieves_label": p.label(u)})
    return sorted(out, key=lambda d: (d["relieves_label"] or "", d["device_label"] or ""))


def relief(conn, e: dict, src=None, plant=None) -> list[dict]:
    """The items relieving into the flare during episode `e` (from a flare flow point): each one's pressure before
    the episode and its peak in it, where that rose by `RISE` or more, the largest rise first."""
    from ..sources import timeseries
    from .calcs import read

    if e.get("source") != "point":
        return []
    by: dict[str, list[dict]] = {}
    for d in paths(conn, e["subject"], plant):
        by.setdefault(d["relieves"], []).append(d)
    if not by:
        return []
    roles = conn.execute("select point_id, asset, role from measurement_role where asset = any(%s) and role = any(%s)",
                         (list(by), list(RELIEF_ROLES))).fetchall()
    pts = {r["point_id"]: pt for r in roles if (pt := timeseries._point(conn, r["point_id"]))}
    if not pts:
        return []
    a, b = datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"])
    vals = read(pts, a - BEFORE, b, STEP_MINUTES, src or timeseries.provider())
    out = []
    for r in roles:
        if r["point_id"] not in pts:
            continue
        series = sorted(vals[r["point_id"]].items())
        before = [v for t, v in series if v is not None and t[:16] < e["start"]]
        during = [v for t, v in series if v is not None and t[:16] >= e["start"]]
        if not before or not during:
            continue
        base, peak = median(before), max(during)
        if base > 0 and peak >= base * (1 + RISE):
            ds = by[r["asset"]]
            out.append({"item": r["asset"], "label": ds[0]["relieves_label"], "point": pts[r["point_id"]]["point"],
                        "role": r["role"], "before": round(base, 2), "peak": round(peak, 2),
                        "uom": pts[r["point_id"]].get("uom"), "rise_pct": round((peak / base - 1) * 100, 1),
                        "devices": [d["device_label"] for d in ds]})
    return sorted(out, key=lambda x: -x["rise_pct"])


def events(conn, until: str | None = None, days: int = DAYS, src=None) -> list[dict]:
    """The flare episodes in the `days` up to `until`, each with the records it was matched to, the latest first."""
    from ..graph import structure
    from .calcs import window_of

    start, end = window_of(conn, days, until)
    out, plant = [], None
    found = _from_points(conn, start, end, src)
    seen = {d for e in found for d in _days(e["start"], e["end"])}
    for e in found + [e for e in _from_reports(conn, start, end) if e["start"][:10] not in seen]:
        causes = attribute(conn, e["start"], e["end"], skip=EVENT_TYPE)
        if e["source"] == "point" and plant is None:
            plant = structure.load(conn)
        out.append({**e, "causes": causes, "attributed": bool(causes), "relief": relief(conn, e, src, plant)})
    return sorted(out, key=lambda e: e["start"], reverse=True)


def _days(start: str, end: str) -> set[str]:
    """The days an episode touches."""
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end) - timedelta(minutes=1)
    return {(a + timedelta(days=i)).date().isoformat() for i in range((b.date() - a.date()).days + 1)}


def key_of(e: dict) -> str:
    return f"{e['label']} · {e['start']}"


def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("flare_event") or exception_type(
        "flare_event", "Flare event", role="role:hse_specialist", audience=("ioc",), joins=False,
        description="A flare above its own baseline, with the downtime, log entries and detections around it, or its "
                    "cause unknown when nothing matches. Evidence for the flaring report: not an emissions inventory "
                    "or a compliance finding.")


EVENT_TYPE = _type().name
_KIND = {"downtime": "Downtime", "log_entry": "Log", "detection": "Detection"}


@detectors.register("flare_events", title="A flare above its baseline, matched to the records around it",
                    kind=detectors.RULE, cadence="daily", raises=(EVENT_TYPE,), roles=("flare_flow",),
                    needs={"a flare flow point or a daily flare volume":
                           "select 1 where exists (select 1 from measurement_role where role = 'flare_flow') or exists "
                           "(select 1 from golden where field = 'flare_ksm3')"},
                    eval="test:tests/analysis/test_flare.py::test_a_flare_event_is_raised_with_its_records_or_unknown",
                    found=lambda out: len(out["raised"]))
def watch(conn, until: str | None = None, days: int = DAYS, by: str = "agent:flare", src=None) -> dict:
    """Each flare episode raised as `flare_event` for the HSE specialist (keyed by where and when it started), with
    the records it was matched to; an episode is a record of what happened, so it stays until a person closes it."""
    from ..core import work
    from ..core.exceptions import ExceptionError, raise_exception

    found_rows = events(conn, until, days, src)
    raised, errors = [], []
    for e in found_rows:
        unit = f" {e['volume_unit']}" if e["volume_unit"] else ""
        causes = tuple(fact(f"{_KIND[c['kind']]}, {c['at'].replace('T', ' ')}", c["text"]) for c in e["causes"][:8])
        relieving = tuple(fact(f"Relieving: {x['label']}", f"{x['point']} up from {x['before']:g} to {x['peak']:g} "
                                                           f"{x['uom'] or ''}".strip() + f" ({x['rise_pct']:g} %), "
                                                           f"through {', '.join(x['devices'])}")
                          for x in e["relief"][:4])
        found = Finding(
            "flare.event", DETERMINISTIC, method="A flare above its own baseline, matched to the records around it",
            start=e["start"], end=e["end"],
            data={"attributed": e["attributed"], "causes": [{"kind": c["kind"], "id": c["id"]} for c in e["causes"]],
                  "relief": [{"item": x["item"], "rise_pct": x["rise_pct"]} for x in e["relief"]],
                  "volume": e["volume"], "peak": e["peak"]},
            items=(fact("Flare", e["label"]), fact("Measured by", e["point"] or "the daily report's flare volume"),
                   at("From", e["start"]), at("To", e["end"]),
                   fact("Peak", f"{e['peak']:g} {e['uom'] or ''}".strip()),
                   fact("Baseline", f"{e['baseline']:g} {e['uom'] or ''}".strip()),
                   fact("Volume over the baseline, an estimate", f"{e['volume']:g}{unit}"),
                   fact("Cause", None if e["attributed"] else "unknown: no downtime, log entry or detection matches"),
                   *relieving, *causes,
                   fact("What this is", "Evidence for the flaring report, not an emissions inventory or a compliance "
                                        "finding."),
                   fact("Data", "synthetic values from the fake plant, not plant data"
                        if e["provider"] == "synthetic" else None)))
        key = key_of(e)
        why = "cause unknown" if not e["attributed"] else f"{len(e['causes'])} records around it"
        try:
            x = raise_exception(conn, EVENT_TYPE, key, f"Flare at {e['label']} from {e['start'].replace('T', ' ')}: "
                                f"about {e['volume']:g}{unit} over its baseline, {why}", by, source="flare",
                                refs=[e["subject"]], priority=0.6 if not e["attributed"] else 0.4, finding=found)
        except (work.WorkError, ExceptionError) as err:
            errors.append(f"{key}: {err}")
            continue
        raised.append({"key": key, "attributed": e["attributed"], "exception_id": x["id"], "item_id": x["item_id"],
                       "new": x["raised"]})
    conn.commit()
    return {"at": until or clock.now().isoformat(timespec="minutes"), "events": len(found_rows), "raised": raised,
            "unknown": sum(1 for e in found_rows if not e["attributed"]), "errors": errors}
