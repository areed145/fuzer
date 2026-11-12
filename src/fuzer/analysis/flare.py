"""Flare event attribution (P3.1e, #351): a flare above its own baseline, matched to the records around it (downtime,
the operator log, other detections at the time), so the flaring report starts from evidence rather than a blank. Read
from flare flow points by measurement role (`flare_flow`) and from the daily reports' flare volume, so nothing here
knows a field, a flare system or a permit.

- `events(conn, until, days)`: each flare episode in the window.
  - On a flare flow point: a run of at least `MIN_STEPS` readings above `SPIKE` times the point's own baseline (its
    median over the window, the purge level), with its peak and the volume over the baseline.
  - On the daily reports: a day whose flare is `DAY_SPIKE` times the median of the `DAY_BASE` days before it.
- `attribute(conn, start, end)`: the downtime events overlapping the episode, the log entries in it that speak of
  flaring, and other detections that started in it. An episode with none is reported with its cause unknown.
- The `flare_events` detector raises each episode as `flare_event` for the HSE specialist, with the records it was
  matched to as evidence. It is evidence only: not an emissions inventory, and not a compliance finding.
"""

import re
from datetime import datetime, timedelta
from statistics import median

from ..core import detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact

SPIKE = 3.0  # times the point's baseline
MIN_STEPS = 2  # readings above it that make an episode
STEP_MINUTES = 10
DAY_SPIKE = 1.5  # a day's flare over the median of the days before it
DAY_BASE = 30
PAD = timedelta(hours=1)  # records this close to an episode's ends belong to it
DAYS = 3
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


def events(conn, until: str | None = None, days: int = DAYS, src=None) -> list[dict]:
    """The flare episodes in the `days` up to `until`, each with the records it was matched to, the latest first."""
    from .calcs import window_of

    start, end = window_of(conn, days, until)
    out = []
    for e in _from_points(conn, start, end, src) + _from_reports(conn, start, end):
        causes = attribute(conn, e["start"], e["end"], skip=EVENT_TYPE)
        out.append({**e, "causes": causes, "attributed": bool(causes)})
    return sorted(out, key=lambda e: e["start"], reverse=True)


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
        found = Finding(
            "flare.event", DETERMINISTIC, method="A flare above its own baseline, matched to the records around it",
            start=e["start"], end=e["end"],
            data={"attributed": e["attributed"], "causes": [{"kind": c["kind"], "id": c["id"]} for c in e["causes"]],
                  "volume": e["volume"], "peak": e["peak"]},
            items=(fact("Flare", e["label"]), fact("Measured by", e["point"] or "the daily report's flare volume"),
                   at("From", e["start"]), at("To", e["end"]),
                   fact("Peak", f"{e['peak']:g} {e['uom'] or ''}".strip()),
                   fact("Baseline", f"{e['baseline']:g} {e['uom'] or ''}".strip()),
                   fact("Volume over the baseline, an estimate", f"{e['volume']:g}{unit}"),
                   fact("Cause", None if e["attributed"] else "unknown: no downtime, log entry or detection matches"),
                   *causes,
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
    return {"at": until or datetime.now().isoformat(timespec="minutes"), "events": len(found_rows), "raised": raised,
            "unknown": sum(1 for e in found_rows if not e["attributed"]), "errors": errors}
