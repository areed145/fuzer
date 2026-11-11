"""Upsets that belong together: what else happened around an upset, how it spread, and the patterns that
repeat, so the IOC triages a cascade once, from where it started, and sends one operator.

The SRE groups detections into one upset while each starts within `sre.group_gap_hours` of the last and sits within
`sre.hops` links of the others. A cascade slower than that, or carried by a link the plant model doesn't hold (power,
instrument air, fuel gas), arrives as several upsets and so several cases. This module puts them back together:

    related(conn, upset_id, hours)       other upsets within `hours` of it, each scored by time, plant-model distance
                                         and how often the two have come together before: a likely cause (earlier and
                                         connected), a likely knock-on (later and connected), seen together before, or
                                         coincident
    merge(conn, upset_id, into, by)      the IOC says it is one event: the upset's case joins the other's (one case,
                                         one dispatch), its own case is cancelled with the reason on both histories
    patterns(conn, since, until)         recurring sequences across history: a finding on one unit followed by a
                                         finding on another within `window_minutes`, how often, and the typical lag
    view(conn, upset_id)                 what the triage page shows: detections in time order, the spread picture,
                                         related upsets and the ones merged in

A signature is (first unit, detector): "a stop on 20-K-2001C" or "an excursion on 20-V-2001". Correlation in time is
a lead, never a cause: each result says which plant-model link supports it, or that none does.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median

from ..core.jsonsafe import plain as _plain
from ..graph.topology import topology

WINDOW_HOURS = 12  # half a shift either side: slow enough for a compressor trip to reach the wells it lifts
HISTORY_DAYS = 90  # how far back "seen together before" looks
MAX_HOPS = 8  # beyond this the plant model says "not connected"
PATTERN_MINUTES = 360  # a knock-on starts within this long of its cause
DETECTOR_WORDS = {"stops": "a stop", "excursions": "an excursion", "steps": "a step change", "drifts": "a drift",
                  "models": "off its curve", "hydraulics": "a hydraulic change", "multivariate": "a multivariate anomaly"}


class CascadeError(Exception):
    """A request the cascade view refused: no such upset, or a merge that can't be made."""


def _hops(adj: dict[str, set[str]], a: list[str], b: list[str], limit: int = MAX_HOPS) -> int | None:
    """The fewest plant-model links between any unit of `a` and any of `b`, or None beyond `limit`."""
    a, b = {x for x in a if x}, {x for x in b if x}
    if not a or not b:
        return None
    if a & b:
        return 0
    seen, frontier = set(a), set(a)
    for n in range(1, limit + 1):
        frontier = {v for u in frontier for v in adj.get(u, ())} - seen
        if frontier & b:
            return n
        if not frontier:
            return None
        seen |= frontier
    return None


def _rows(conn, where: str, params: dict) -> list[dict]:
    return [dict(r) for r in conn.execute(
        f"""
        select u.id, u.kind, u.status, u.start, u."end", u.units, u.detections, u.work_item_id, u.priority_usd,
               u.merged_into, u.first_unit, l.label as first_unit_label, d.detector, d.key, p.label as first_point,
               w.state as case_state, w.title as case_title
        from upset u left join detection d on d.id = u.first_detection
        left join entity_label l on l.fuzer_id = u.first_unit left join entity_label p on p.fuzer_id = d.point_id
        left join work_item w on w.id = u.work_item_id
        where {where}
        order by u.start
        """, params)]


def _one(conn, upset_id: int) -> dict:
    rows = _rows(conn, "u.id = %(id)s", {"id": upset_id})
    if not rows:
        raise CascadeError(f"no upset {upset_id}")
    return rows[0]


def signature(u: dict) -> str:
    """A finding on a unit as words: "a stop on 20-K-2001C"."""
    what = DETECTOR_WORDS.get(u.get("detector") or "", u.get("detector") or "an upset")
    return f"{what} on {u.get('first_unit_label') or u.get('first_point') or 'an unknown unit'}"


def _history(conn, a: dict, b: dict, window: timedelta, before: datetime) -> int:
    """How many times in the HISTORY_DAYS before `before` an upset with a's signature was followed (or preceded) within
    `window` by one with b's: the two signatures coming together, not counting this pair."""
    if not (a.get("first_unit") and b.get("first_unit")):
        return 0
    return conn.execute(
        """
        select count(distinct x.id) as n
        from upset x join detection xd on xd.id = x.first_detection
        join upset y on y.id <> x.id and y.start between x.start - %(w)s and x.start + %(w)s
        join detection yd on yd.id = y.first_detection
        where x.kind = 'process' and y.kind = 'process'
          and x.first_unit = %(au)s and xd.detector = %(ad)s and y.first_unit = %(bu)s and yd.detector = %(bd)s
          and x.start >= %(before)s::timestamp - make_interval(days => %(days)s) and x.start < %(before)s
          and x.id <> all(%(ids)s) and y.id <> all(%(ids)s)
        """, {"w": window, "au": a["first_unit"], "ad": a["detector"], "bu": b["first_unit"], "bd": b["detector"],
              "before": before, "days": HISTORY_DAYS, "ids": [a["id"], b["id"]]}).fetchone()["n"]


def _role(lag: float, hops: int | None, together: int) -> str:
    if hops is not None and lag < 0:
        return "likely_cause"
    if hops is not None and lag > 0:
        return "likely_knock_on"
    if hops is not None:
        return "same_time"
    return "seen_together" if together >= 2 else "coincident"


ROLE_WORDS = {"likely_cause": "Likely cause: started earlier, connected in the plant model",
              "likely_knock_on": "Likely knock-on: started later, connected in the plant model",
              "same_time": "Same time, connected in the plant model",
              "seen_together": "Not connected in the plant model, but seen together before",
              "coincident": "Coincident: no plant-model link and no history together"}


def related(conn, upset_id: int, hours: float = WINDOW_HOURS, limit: int = 12, adj: dict | None = None) -> dict:
    """Other process upsets starting within `hours` of this one, most likely related first. Each: its signature, lag
    (minutes, negative = started earlier), plant-model hops between their units (None = not connected within
    MAX_HOPS), how often the two signatures came together before, a role and a score in [0, 1]."""
    me = _one(conn, upset_id)
    window = timedelta(hours=hours)
    adj = topology(conn) if adj is None else adj
    out = []
    for o in _rows(conn, "u.id <> %(id)s and u.kind = 'process' and u.start between %(a)s and %(b)s",
                   {"id": upset_id, "a": me["start"] - window, "b": me["start"] + window}):
        lag = (o["start"] - me["start"]).total_seconds() / 60
        hops = _hops(adj, me["units"], o["units"])
        earlier, later = (o, me) if lag < 0 else (me, o)
        together = _history(conn, earlier, later, window, min(me["start"], o["start"]))
        closeness = max(0.0, 1 - abs(lag) / (hours * 60))
        link = 1 / (1 + hops) if hops is not None else 0.15
        score = min(1.0, closeness * link + 0.1 * min(together, 5))
        role = _role(lag, hops, together)
        out.append({"id": o["id"], "signature": signature(o), "start": o["start"], "end": o["end"],
                    "lag_minutes": round(lag), "hops": hops, "seen_together": together, "role": role,
                    "why": ROLE_WORDS[role], "score": round(score, 3), "status": o["status"],
                    "detections": o["detections"], "units": len(o["units"]), "work_item_id": o["work_item_id"],
                    "case_title": o["case_title"], "case_state": o["case_state"], "merged_into": o["merged_into"],
                    "same_case": bool(o["work_item_id"]) and o["work_item_id"] == me["work_item_id"]})
    out.sort(key=lambda r: -r["score"])
    return _plain({"upset": upset_id, "signature": signature(me), "start": me["start"], "window_hours": hours,
                   "related": out[:limit], "considered": len(out),
                   "basis": f"process upsets starting within {hours:g} h; plant-model links: part of, flows to, lift "
                            f"gas, P&ID piping and train; history over {HISTORY_DAYS} days. Time and links are leads, "
                            "not causes."})


def _exception(conn, upset_id: int) -> dict | None:
    return conn.execute("select * from exception_record where type = 'upset' and key = %s "
                        "order by (state = 'open') desc, id desc limit 1", (f"upset:{upset_id}",)).fetchone()


def merge(conn, upset_id: int, into: int, by: str, reason: str | None = None) -> dict:
    """The IOC says `upset_id` is part of the same event as `into`: its case joins `into`'s (one case to triage, one
    dispatch, one close-out) and its own case is cancelled. Both histories say so, and the upset records it."""
    from . import exceptions

    if upset_id == into:
        raise CascadeError("an upset can't be merged into itself")
    me, host = _one(conn, upset_id), _one(conn, into)
    mine, theirs = _exception(conn, upset_id), _exception(conn, into)
    if not theirs or not theirs["item_id"]:
        raise CascadeError(f"upset {into} has no case to merge into yet")
    if not mine:
        raise CascadeError(f"upset {upset_id} has no case yet: it hasn't been handed to the IOC")
    why = reason or f"same event as upset {into}: {signature(host)}"
    row = exceptions.join(conn, mine["id"], theirs["id"], by, reason=why)
    conn.execute("update upset set merged_into = %s, work_item_id = %s, updated_at = now() where id = %s",
                 (into, row["item_id"], upset_id))
    conn.commit()
    return {"upset": upset_id, "into": into, "item_id": row["item_id"], "cancelled_case": me["work_item_id"]
            if me["work_item_id"] != row["item_id"] else None, "reason": why}


def merged(conn, upset_id: int) -> list[dict]:
    """The upsets merged into this one."""
    return _plain([{"id": r["id"], "signature": signature(r), "start": r["start"]}
                   for r in _rows(conn, "u.merged_into = %(id)s", {"id": upset_id})])


def view(conn, upset_id: int) -> dict:
    """What the triage page shows for an upset: its detections in time order, the spread picture (None when it
    reached no unit), related upsets and the ones merged into it."""
    from ..plots import ChartError, troubleshooting
    from . import sre

    detail = sre.upset_detail(conn, upset_id, limit=200)
    if detail is None:
        raise CascadeError(f"no upset {upset_id}")
    try:
        fig = troubleshooting.propagation(conn, upset_id)
        spread = {"spec": fig.spec, "summary": fig.summary, "title": fig.title}
    except ChartError as e:
        spread = {"spec": None, "summary": None, "title": None, "note": str(e)}
    return _plain({"upset": {k: detail.get(k) for k in ("id", "kind", "status", "start", "end", "detections",
                                                        "first_unit", "first_point", "first_key", "first_detector",
                                                        "units", "work_item_id", "priority_usd")},
                   "signature": signature(_one(conn, upset_id)),
                   "timeline": [{**{k: d.get(k) for k in ("id", "start", "end", "unit", "point", "detector", "key",
                                                          "magnitude")}, "finding": sre.describe(d.get("key"))}
                                for d in detail["timeline"]],
                   "detections_shown": detail["detections_shown"], "by_unit": detail["by_unit"],
                   "spread": spread, "related": related(conn, upset_id), "merged": merged(conn, upset_id)})


def patterns(conn, since: str | None = None, until: str | None = None, window_minutes: int = PATTERN_MINUTES,
             min_support: int = 2, top: int = 25, connected_only: bool = True) -> dict:
    """Recurring cascades: a finding on one unit followed within `window_minutes` by a finding on another, counted
    across the process upsets in [since, until). Each pattern: the two signatures, how many times the second followed
    the first (`support`), how often the first happened at all, the share it was followed (`confidence`), the median
    and range of lags, hops between them and the upsets it rests on. `connected_only` keeps pairs the plant model
    links within MAX_HOPS."""
    ups = _rows(conn, "u.kind = 'process' and u.first_unit is not null and d.detector is not null "
                      "and (%(s)s::timestamp is null or u.start >= %(s)s::timestamp) "
                      "and (%(u)s::timestamp is null or u.start < %(u)s::timestamp)", {"s": since, "u": until})
    adj = topology(conn)
    window = timedelta(minutes=window_minutes)
    sig = lambda u: (u["first_unit"], u["detector"])
    count = defaultdict(int)
    for u in ups:
        count[sig(u)] += 1
    pairs: dict[tuple, dict] = {}
    hop_cache: dict[tuple, int | None] = {}
    for i, a in enumerate(ups):
        followed: set[tuple] = set()  # count each knock-on signature once per cause
        for b in ups[i + 1:]:
            if b["start"] - a["start"] > window:
                break
            if sig(b) == sig(a) or sig(b) in followed:
                continue
            k = (a["first_unit"], b["first_unit"])
            if k not in hop_cache:
                hop_cache[k] = _hops(adj, [a["first_unit"]], [b["first_unit"]])
            if connected_only and hop_cache[k] is None:
                continue
            followed.add(sig(b))
            p = pairs.setdefault((sig(a), sig(b)), {"first": signature(a), "then": signature(b), "lags": [],
                                                    "hops": hop_cache[k], "examples": [], "_a": sig(a)})
            p["lags"].append((b["start"] - a["start"]).total_seconds() / 60)
            p["examples"].append({"cause": a["id"], "knock_on": b["id"], "start": a["start"]})
    out = []
    for p in pairs.values():
        n = len(p["lags"])
        if n < min_support:
            continue
        out.append({"first": p["first"], "then": p["then"], "support": n, "first_seen": count[p["_a"]],
                    "confidence": round(n / count[p["_a"]], 2), "median_lag_minutes": round(median(p["lags"])),
                    "lag_range_minutes": [round(min(p["lags"])), round(max(p["lags"]))], "hops": p["hops"],
                    "examples": p["examples"][-5:]})
    out.sort(key=lambda r: (-r["support"], -r["confidence"]))
    return _plain({"value": len(out), "unit": "recurring cascades", "patterns": out[:top], "upsets": len(ups),
                   "window_minutes": window_minutes, "min_support": min_support,
                   "method": f"ordered pairs of upset signatures (first unit, detector) starting within "
                             f"{window_minutes} min of each other"
                             + (f", linked in the plant model within {MAX_HOPS} hops" if connected_only else "")
                             + f", kept when seen {min_support}+ times. Sequence in time is a lead, not a cause."})
