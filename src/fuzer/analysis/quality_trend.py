"""Separator and export quality trend (#342): each lab analyte at each sample point over a window (oil in water off
a separator, BS&W, salt and density in the oil, water dew point and H2S in the gas), with its level before against
its latest, the gaps where samples stopped coming, and the process events around it. The `quality_drift` detector
(quality.drifts, P3.1d) says which series moved off their own baseline; this is what an engineer reads to see them.

    trends(conn, since, until)                every series in the window, drifting ones first
    series(conn, subject, sample_point, analyte, since, until)   one series' samples, gaps and the events around it

No claim of on or off specification is made: export specifications and discharge limits are not loaded, so every
result says the limits are absent and shows the trend against itself only.
"""

from datetime import datetime
from itertools import pairwise
from statistics import median

from ..graph import operations as ops
from . import quality
from .calcs import CalcError, _label

GAP_FACTOR = 2.5  # an interval this many times the series' usual one is a gap in sampling
NO_LIMITS = "no export specification or discharge limit is loaded: trend only, no on or off specification claim"


def _window(conn, since, until, days) -> tuple[datetime, datetime]:
    since, until = ops._window(conn, since, until, days)
    return datetime.fromisoformat(since), datetime.fromisoformat(until)


def _gaps(samples: list[dict]) -> tuple[float | None, list[dict]]:
    ats = [s["at"] for s in samples]
    steps = [(b - a).total_seconds() / 86400 for a, b in pairwise(ats)]
    if not steps:
        return None, []
    usual = median(steps)
    gaps = [{"from": a.date().isoformat(), "to": b.date().isoformat(), "days": round(d, 1)}
            for (a, b), d in zip(pairwise(ats), steps, strict=True) if usual and d > GAP_FACTOR * usual]
    return round(usual, 1), gaps


def _summary(samples: list[dict]) -> dict:
    vals = [s["value"] for s in samples if s["value"] is not None]
    half = max(1, len(vals) // 2)
    before, latest = vals[:half], vals[-quality.LAST:]
    ref, now = median(before), median(latest)
    usual, gaps = _gaps(samples)
    return {"samples": len(samples), "first": samples[0]["at"].date().isoformat(),
            "last": samples[-1]["at"].date().isoformat(), "latest": round(vals[-1], 3), "low": round(min(vals), 3),
            "high": round(max(vals), 3), "first_half_median": round(ref, 3),
            f"latest_{quality.LAST}_median": round(now, 3),
            "change_pct": round(100 * (now - ref) / abs(ref), 1) if ref else None,
            "below_detection": sum(1 for s in samples if s["qualifier"]), "usual_interval_days": usual,
            "gaps": gaps}


def trends(conn, since: str | None = None, until: str | None = None, days: int = 365) -> dict:
    """Every lab series (what was sampled, where, which analyte) with samples in [since, until), with the detector's
    drifts first."""
    a, b = _window(conn, since, until, days)
    drifting = {(d["subject"], d["sample_point"], d["analyte"]): d for d in quality.drifts(conn, b.isoformat())}
    rows = []
    for (subject, point, analyte), samples in quality._series(conn, b).items():
        inside = [s for s in samples if s["at"] >= a]
        if not inside:
            continue
        d = drifting.get((subject, point, analyte))
        rows.append({"subject": subject, "label": inside[-1]["label"], "sample_point": point, "analyte": analyte,
                     "unit": inside[-1]["unit"], "method": inside[-1]["method"], **_summary(inside),
                     "drift": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items()
                               if k in ("direction", "level", "baseline", "change", "since", "held")} if d else None})
    rows.sort(key=lambda r: (r["drift"] is None, -abs(r["change_pct"] or 0), r["sample_point"], r["analyte"]))
    return {"since": a.date().isoformat(), "until": b.date().isoformat(), "series": rows,
            "summary": {"series": len(rows), "sample_points": len({(r["subject"], r["sample_point"]) for r in rows}),
                        "drifting": sum(1 for r in rows if r["drift"]),
                        "with_gaps": sum(1 for r in rows if r["gaps"]), "limits_loaded": 0},
            "limits": None, "limit_basis": NO_LIMITS, "gap_factor": GAP_FACTOR,
            "source": "lab (LIMS) results by sample point and analyte",
            "note": "Each analyte against itself: its first half's median in the window against its latest samples' "
                    f"median, and gaps where an interval ran past {GAP_FACTOR:g} times its usual one. A drift is the "
                    "quality_drift detector's finding. " + NO_LIMITS[0].upper() + NO_LIMITS[1:] + "."}


def _events(conn, subject: str, a: datetime, b: datetime) -> list[dict]:
    """Downtime on the sampled equipment or field-wide (on a facility) in the window, the process events to read the
    samples against."""
    rows = conn.execute(
        "select d.fuzer_id as id, t.kind, coalesce(l.label, t.fuzer_id) as on_label, jsonb_object_agg(g.field, g.value) as f "
        "from entity d join golden g using (fuzer_id) join edge e on e.src = d.fuzer_id and e.rel in ('affects', "
        "'caused_by') join entity t on t.fuzer_id = e.dst left join entity_label l on l.fuzer_id = t.fuzer_id "
        "where d.kind = 'downtime' and d.retired_at is null and (t.fuzer_id = %s or t.kind = 'facility') "
        "group by d.fuzer_id, t.kind, t.fuzer_id, l.label", (subject,)).fetchall()
    out = []
    for r in rows:
        start = str(r["f"].get("start") or "")[:16]
        if start and a.isoformat() <= start < b.isoformat():
            out.append({"id": r["id"], "start": start, "end": str(r["f"].get("end") or "")[:16] or None,
                        "on": r["on_label"], "field_wide": r["kind"] == "facility",
                        "reason": r["f"].get("reason") or r["f"].get("reason_code"),
                        "description": r["f"].get("description") or r["f"].get("comment")})
    return sorted(out, key=lambda e: e["start"])


def series(conn, subject: str, sample_point: str, analyte: str, since: str | None = None, until: str | None = None,
           days: int = 365) -> dict:
    """One series' samples in the window, its gaps, the detector's drift if any, and the downtime around it."""
    a, b = _window(conn, since, until, days)
    samples = [s for s in quality._series(conn, b).get((subject, sample_point, analyte), []) if s["at"] >= a]
    if not samples:
        raise CalcError(f"no {analyte} sample at {sample_point} on that item in the window")
    d = next((x for x in quality.drifts(conn, b.isoformat()) if (x["subject"], x["sample_point"], x["analyte"])
              == (subject, sample_point, analyte)), None)
    return {"subject": subject, "label": _label(conn, subject), "sample_point": sample_point, "analyte": analyte,
            "unit": samples[-1]["unit"], "since": a.date().isoformat(), "until": b.date().isoformat(),
            **_summary(samples),
            "points": [{"at": s["at"].isoformat(timespec="minutes"), "value": s["value"],
                        "below_detection": bool(s["qualifier"])} for s in samples],
            "drift": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items()
                      if k in ("direction", "level", "baseline", "change", "since", "held", "low", "high")} if d else None,
            "events": _events(conn, subject, a, b), "limits": None, "limit_basis": NO_LIMITS,
            "note": "Against itself, not a limit: " + NO_LIMITS + "."}


def find(conn, sample_point: str, analyte: str) -> str | None:
    """The subject a sample point and analyte are sampled on (the latest one, if several)."""
    r = conn.execute("select fuzer_id from measurement where location = %s and quantity = %s order by at desc limit 1",
                     (sample_point, analyte)).fetchone()
    return r["fuzer_id"] if r else None


