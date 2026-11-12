"""Before and after intervention gain (P7.5a, #340): did the well work pay off? For each recorded intervention on a well
(a downtime record in the well category that affects it: slickline, a scale squeeze, a valve change, a survey), the
oil rate, water cut and GOR from the tests before set against the tests after.

The oil gain is net of decline: an exponential decline is fitted through the tests before (forecast.arps_interval
gives its 90 % range), and each test after is set against where that decline would have put the well that day. A well
that kept declining exactly as before gained nothing, even if its rate after is lower than before.

    gained          the tests after sit above the decline's range on average: the work paid off
    lost            below it: the well came back worse (a lead, not proof)
    no_clear_change within the range
    unknown         no test before or after, or too few before to fit a decline; never zero

Water cut and GOR are the mean after less the mean before (no trend taken out). Attribution is not proof: the
accountable engineer decides on further work.
"""

import math
from datetime import date, datetime, timedelta

from ..graph import operations as ops
from ..graph.queries import _DOWNTIME
from . import calcs, forecast
from .calcs import YEAR

BEFORE_DAYS = 180  # tests before an intervention that set its trend
AFTER_DAYS = 120  # tests after it that show the result
MIN_BEFORE = 3  # tests before needed to net out decline


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _rows(conn, since: str, until: str, well: str | None) -> list[dict]:
    return conn.execute(
        _DOWNTIME + """
        select dd.id, dd.f, w.fuzer_id as well, coalesce(l.label, w.fuzer_id) as label from dd
        join edge a on a.src = dd.id and a.rel = 'affects'
        join entity w on w.fuzer_id = a.dst and w.kind = 'well'
        left join entity_label l on l.fuzer_id = w.fuzer_id
        where coalesce(dd.f->>'reason_category', '') = 'well' and (%(well)s::text is null or w.fuzer_id = %(well)s)
        order by dd.f->>'start'
        """, {"id": None, "since": since, "until": until, "well": well}).fetchall()


def one(conn, row: dict) -> dict:
    """One intervention's before and after."""
    f = row["f"]
    start = datetime.fromisoformat(str(f["start"])[:19])
    end = datetime.fromisoformat(str(f.get("end") or f["start"])[:19])
    base = {"id": row["id"], "well_id": row["well"], "well": row["label"], "start": start.isoformat()[:16],
            "end": end.isoformat()[:16], "reason": f.get("reason"), "comment": f.get("comment") or f.get("text"),
            "lost_oil_sm3": calcs._r(ops._num(f.get("lost_oil_sm3")), 1)}
    try:
        tests = [t for t in calcs.well_tests(conn, row["well"]) if t.get("oil_rate", 0) > 0]
    except calcs.CalcError:
        tests = []
    before = [t for t in tests if start - timedelta(days=BEFORE_DAYS) <= t["at"] < start]
    after = [t for t in tests if end < t["at"] <= end + timedelta(days=AFTER_DAYS)]
    base |= {"tests_before": len(before), "tests_after": len(after),
             "window": {"before_from": (start - timedelta(days=BEFORE_DAYS)).date().isoformat(),
                        "after_to": (end + timedelta(days=AFTER_DAYS)).date().isoformat()},
             "refs": [row["id"], row["well"]] + [t["record"] for t in before[-2:] + after[:2]]}
    for key, q in (("water_cut", "water_cut_change_pct"), ("gor", "gor_change")):
        b, a = _mean([t[key] for t in before if t.get(key) is not None]), _mean([t[key] for t in after if t.get(key) is not None])
        base[q] = calcs._r(a - b, 1) if a is not None and b is not None else None
    if not before or not after:
        side = "before" if not before else "after"
        return base | {"verdict": "unknown", "why": f"no well test {side} it within {BEFORE_DAYS if side == 'before' else AFTER_DAYS} days"}
    enough = forecast.sufficiency([t["at"] for t in before], MIN_BEFORE)
    if not enough["quotable"]:
        return base | {"verdict": "unknown", "why": "too few tests before it to net out decline: " + "; ".join(enough["reasons"])}
    t0 = before[0]["at"]
    ts, qs = [(t["at"] - t0).days / YEAR for t in before], [t["oil_rate"] for t in before]
    fit = calcs.arps(ts, qs, "exponential")
    points = []
    for t in after:
        b = forecast.arps_interval(fit, ts, qs, (t["at"] - t0).days / YEAR)
        points.append((t["oil_rate"], b["value"], b["range"]))
    actual, expected = _mean([p[0] for p in points]), _mean([p[1] for p in points])
    low, high = (_mean([p[2][0] for p in points]), _mean([p[2][1] for p in points])) if points[0][2] else (None, None)
    gain = actual - expected
    verdict = ("gained" if high is not None and actual > high else "lost" if low is not None and actual < low
               else "no_clear_change")
    return base | {"verdict": verdict, "oil_before_sm3d": calcs._r(before[-1]["oil_rate"], 1),
                   "oil_after_sm3d": calcs._r(actual, 1), "oil_expected_sm3d": calcs._r(expected, 1),
                   "oil_range_sm3d": [calcs._r(low, 1), calcs._r(high, 1)] if low is not None else None,
                   "gain_sm3d": calcs._r(gain, 1),
                   "decline_pct_per_year": calcs._r((1 - math.exp(-fit["D"])) * 100, 1), "level": forecast.LEVEL}


def gains(conn, since: str | None = None, until: str | None = None, well: str | None = None, days: int = 730) -> dict:
    """Every recorded well intervention starting in [since, until) (default the `days` to the latest daily report),
    with its gain net of decline, latest first, and the totals over those that can be said."""
    until = until or (date.fromisoformat(ops.as_of(conn)) + timedelta(days=1)).isoformat()
    since = since or (date.fromisoformat(until) - timedelta(days=days)).isoformat()
    items = [one(conn, r) for r in _rows(conn, since, until, well)]
    said = [i for i in items if i["verdict"] != "unknown"]
    return {"since": since, "until": until, "unit": "Sm3/d oil", "source": "downtime records in the well category "
            "(the interventions) and well tests before and after", "level": forecast.LEVEL,
            "interventions": sorted(items, key=lambda i: i["start"], reverse=True),
            "summary": {"interventions": len(items), "said": len(said), "unknown": len(items) - len(said),
                        "gained": sum(1 for i in said if i["verdict"] == "gained"),
                        "lost": sum(1 for i in said if i["verdict"] == "lost"),
                        "no_clear_change": sum(1 for i in said if i["verdict"] == "no_clear_change"),
                        "net_gain_sm3d": calcs._r(sum(i["gain_sm3d"] for i in said), 1) if said else None},
            "note": "Gain is the oil rate after against the decline before, net of that decline. Attribution is not "
                    "proof; the accountable engineer decides on further work. Unknown is never counted as zero."}
