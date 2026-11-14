"""Flare attribution over a review window (#351): the daily flare against its own baseline, every flare episode the
`flare_events` detector's method finds (flare.events, P3.1e) with the records it was matched to, and how much of the
flare over the baseline those records explain. For the environmental and HSE review; evidence only, never an emissions
inventory or a compliance finding.

    review(conn, since, until)   the daily flare, its rolling baseline, the episodes with their causes, and a summary
    systems(conn)                each metered flare and what is piped into it: the relief, blowdown and control valves
                                 and the items they relieve, by area
    allocation(conn, since, until)  the flare the production accounting system allocated back to each source
                                 (measurements `flare_allocated`), by source and kind of flaring

Permit limits are not loaded, so nothing here says a flare was within or over one.
"""

from datetime import date, timedelta
from statistics import median

from ..graph import operations as ops
from . import flare

EVIDENCE = "Evidence for the flaring report, not an emissions inventory or a compliance finding; no permit limit is loaded."


def review(conn, since: str | None = None, until: str | None = None, days: int = 365) -> dict:
    """The flare in [since, until) (default the `days` to the latest daily report): each day against the median of
    the days before it, and every episode with the downtime, log entries and detections around it."""
    since, until = ops._window(conn, since, until, days)
    first = (date.fromisoformat(since) - timedelta(days=flare.DAY_BASE)).isoformat()
    booked = sorted((r["f"]["report_date"][:10], ops._num(r["f"].get("flare_ksm3"))) for r in ops._rows(
        conn, "production_day", "f->>'report_date' >= %(s)s and f->>'report_date' < %(u)s and f ? 'flare_ksm3'",
        {"s": first, "u": until}))
    daily = []
    for i, (day, v) in enumerate(booked):
        if day < since:
            continue
        before = [x for _, x in booked[max(0, i - flare.DAY_BASE):i]]
        daily.append({"day": day, "flare_ksm3": v, "baseline_ksm3": median(before) if before else None})
    n = (date.fromisoformat(until) - date.fromisoformat(since)).days
    episodes = [e for e in flare.events(conn, until + "T00:00", n) if since <= e["start"][:10] < until]
    reasons: dict[str, float] = {}
    for e in episodes:
        why = next((c["text"].split(" ", 1)[0] for c in e["causes"] if c["kind"] == "downtime"), None) or (
            "log entry or detection only" if e["attributed"] else "unknown")
        if e["volume_unit"] == "kSm3":
            reasons[why] = reasons.get(why, 0.0) + e["volume"]
    total = sum(d["flare_ksm3"] for d in daily)
    over = sum(e["volume"] for e in episodes if e["volume_unit"] == "kSm3")
    explained = sum(e["volume"] for e in episodes if e["volume_unit"] == "kSm3" and e["attributed"])
    return {"since": since, "until": until, "days": daily,
            "episodes": [{k: e[k] for k in ("source", "subject", "label", "point", "start", "end", "peak", "baseline",
                                           "uom", "volume", "volume_unit", "attributed")}
                         | {"causes": e["causes"][:8]} for e in episodes],
            "summary": {"days": len(daily), "flare_ksm3": round(total, 1), "episodes": len(episodes),
                        "attributed": sum(1 for e in episodes if e["attributed"]),
                        "unknown": sum(1 for e in episodes if not e["attributed"]),
                        "over_baseline_ksm3": round(over, 1), "explained_ksm3": round(explained, 1),
                        "explained_share_pct": round(100 * explained / over, 1) if over else None,
                        "by_reason_ksm3": {k: round(v, 1) for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])}},
            "systems": systems(conn), "allocation": allocation(conn, since, until),
            "thresholds": {"day_spike": flare.DAY_SPIKE, "day_base": flare.DAY_BASE, "spike": flare.SPIKE},
            "source": "daily report flare volume and flare flow points, downtime, operator log, detections",
            "note": f"A day is an episode when its flare is {flare.DAY_SPIKE:g} times the median of the "
                    f"{flare.DAY_BASE} days before it. Causes are the records around an episode, matched by time: "
                    "a lead for the report, not a proven cause. " + EVIDENCE}


def _names(conn, ids) -> dict[str, str]:
    """Each entity's description where it has one (an area's full name beside its code)."""
    return {r["fuzer_id"]: r["v"] for r in conn.execute(
        "select fuzer_id, value #>> '{}' as v from golden where field = 'description' and fuzer_id = any(%s)",
        (list(ids),)) if r["v"]}


def systems(conn) -> list[dict]:
    """Each flare with a flow point (`flare_flow`) and the devices piped into it from outside its own area, grouped by
    the area of the item each relieves: where a flare's gas can come from, read from the piping."""
    from ..graph import structure

    rows = flare._points(conn)
    if not rows:
        return []
    plant = structure.load(conn)
    out = []
    for r in rows:
        by: dict[str, dict] = {}
        for d in flare.paths(conn, r["asset"], plant):
            area = plant.area.get(d["relieves"])
            g = by.setdefault(area or "", {"area": area, "area_label": plant.label(area) if area else None,
                                           "devices": [], "items": []})
            g["devices"].append(d["device_label"] or d["device"])
            if (d["relieves_label"] or d["relieves"]) not in g["items"]:
                g["items"].append(d["relieves_label"] or d["relieves"])
        names = _names(conn, [g["area"] for g in by.values() if g["area"]])
        for g in by.values():
            g["area_name"] = names.get(g["area"]) or g["area_label"]
        out.append({"subject": r["asset"], "label": r["label"], "point": r["point_id"],
                    "sources": sorted(by.values(), key=lambda g: g["area_label"] or ""),
                    "devices": sum(len(g["devices"]) for g in by.values())})
    return out


def allocation(conn, since: str, until: str) -> dict | None:
    """The flare allocated back to its sources in [since, until): each source's volume, split by kind of flaring and
    by basis (metered or estimated), as the production accounting system booked it. None when nothing is loaded."""
    rows = conn.execute(
        """
        select m.fuzer_id, coalesce(l.label, m.fuzer_id) as label, m.method, sum(m.value) as v, max(m.unit) as unit
        from measurement m
        left join lateral (select label from entity_label where fuzer_id = m.fuzer_id order by label limit 1) l on true
        where m.quantity = 'flare_allocated' and m.at >= %s and m.at < %s
        group by 1, 2, 3
        """, (since, until)).fetchall()
    if not rows:
        return None
    by: dict[str, dict] = {}
    for r in rows:
        kind, basis, *_ = [x.strip() for x in (r["method"] or "").removeprefix("flare allocation:").split(";")] + ["", ""]
        g = by.setdefault(r["fuzer_id"], {"source": r["fuzer_id"], "label": r["label"], "ksm3": 0.0, "by_kind": {},
                                          "by_basis": {}})
        g["ksm3"] += float(r["v"])
        g["by_kind"][kind or "unstated"] = g["by_kind"].get(kind or "unstated", 0.0) + float(r["v"])
        g["by_basis"][basis or "unstated"] = g["by_basis"].get(basis or "unstated", 0.0) + float(r["v"])
    total = sum(g["ksm3"] for g in by.values())
    names = _names(conn, by)
    for g in by.values():
        g["name"] = names.get(g["source"]) or g["label"]
    sources = sorted(({**g, "ksm3": round(g["ksm3"], 1), "share_pct": round(100 * g["ksm3"] / total, 1) if total else None,
                       "by_kind": {k: round(v, 1) for k, v in sorted(g["by_kind"].items(), key=lambda kv: -kv[1])},
                       "by_basis": {k: round(v, 1) for k, v in g["by_basis"].items()}} for g in by.values()),
                     key=lambda g: -g["ksm3"])
    return {"ksm3": round(total, 1), "unit": "kSm3", "sources": sources,
            "source": "production accounting's daily flare allocation"}
