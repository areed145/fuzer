"""Flare attribution over a review window (#351): the daily flare against its own baseline, every flare episode the
`flare_events` detector's method finds (flare.events, P3.1e) with the records it was matched to, and how much of the
flare over the baseline those records explain. For the environmental and HSE review; evidence only, never an emissions
inventory or a compliance finding.

    review(conn, since, until)   the daily flare, its rolling baseline, the episodes with their causes, and a summary

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
            "thresholds": {"day_spike": flare.DAY_SPIKE, "day_base": flare.DAY_BASE, "spike": flare.SPIKE},
            "source": "daily report flare volume and flare flow points, downtime, operator log, detections",
            "note": f"A day is an episode when its flare is {flare.DAY_SPIKE:g} times the median of the "
                    f"{flare.DAY_BASE} days before it. Causes are the records around an episode, matched by time: "
                    "a lead for the report, not a proven cause. " + EVIDENCE}
