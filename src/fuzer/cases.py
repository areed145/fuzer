"""Workflow and agent tools (#105 F): did the fix work, and what fixed it before.

- `compare_periods(ref, a, b)`: each point's median and mean in two windows (before and after a choke
  move, a repair, a swap), the change, and whether it is significant. The test is Welch's t, with each
  window's sample size cut to its effective size for autocorrelation (n (1 - r1) / (1 + r1)), since
  process values sampled every 10 minutes aren't independent. It closes the loop on "did the fix work"
  when a work item closes.
- `similar_cases(ref)`: past incidents like this one (same unit or class, failure modes and event
  types, queries.similar_incidents), each with the work orders raised for it: what was done, its
  activity, and whether the same unit came back within 90 days (the fix didn't hold). It takes an
  incident, an upset (its incident), or a tag or well (its latest incident).
"""

from datetime import datetime, timedelta

import numpy as np

from .calcs import CalcError, _label, read, result

P_SIGNIFICANT = 0.05


def _effective_n(x: np.ndarray) -> float:
    if len(x) < 3 or np.std(x[:-1]) == 0 or np.std(x[1:]) == 0:
        return float(len(x))
    r1 = float(np.corrcoef(x[:-1], x[1:])[0, 1])
    r1 = min(max(r1, 0.0), 0.99)
    return max(2.0, len(x) * (1 - r1) / (1 + r1))


def compare_periods(conn, ref: str, a: tuple[str, str], b: tuple[str, str], step: int = 10, src=None) -> dict:
    """Each point on `ref` (a PI point, or every point on a unit) in window a against window b."""
    from scipy import stats

    from . import signals

    pts = signals._points(conn, ref)
    if not pts:
        raise CalcError("no PI points on that ref")
    wa = [datetime.fromisoformat(x) for x in a]
    wb = [datetime.fromisoformat(x) for x in b]
    if wa[1] <= wa[0] or wb[1] <= wb[0]:
        raise CalcError("each window must end after it starts")
    va = read({p["id"]: p for p in pts}, wa[0], wa[1], step, src)
    vb = read({p["id"]: p for p in pts}, wb[0], wb[1], step, src)
    rows = []
    for p in pts:
        x, y = np.array(list(va[p["id"]].values()), float), np.array(list(vb[p["id"]].values()), float)
        x, y = x[~np.isnan(x)], y[~np.isnan(y)]
        if len(x) < 5 or len(y) < 5:
            continue
        na, nb = _effective_n(x), _effective_n(y)
        se = np.sqrt(np.var(x, ddof=1) / na + np.var(y, ddof=1) / nb)
        t = (y.mean() - x.mean()) / se if se > 0 else 0.0
        dof = se ** 4 / ((np.var(x, ddof=1) / na) ** 2 / (na - 1) + (np.var(y, ddof=1) / nb) ** 2 / (nb - 1)) if se > 0 else 1
        pval = float(2 * stats.t.sf(abs(t), dof)) if se > 0 else 1.0
        rows.append({"point": p["point"], "point_id": p["id"], "description": p["description"], "uom": p["uom"],
                     "before_median": round(float(np.median(x)), 3), "after_median": round(float(np.median(y)), 3),
                     "change": round(float(np.median(y) - np.median(x)), 3),
                     "change_pct": round(float((np.median(y) - np.median(x)) / np.median(x) * 100), 1) if np.median(x) else None,
                     "p_value": round(pval, 4), "significant": pval < P_SIGNIFICANT, "effective_n": [round(na), round(nb)]})
    if not rows:
        raise CalcError("no values in those windows")
    rows.sort(key=lambda r: (not r["significant"], r["p_value"]))
    sig = [r for r in rows if r["significant"]]
    return result(len(sig), None, "changed" if sig else "no_significant_change",
                  f"median of each point in {a[0]} to {a[1]} against {b[0]} to {b[1]}; Welch's t on the means with each "
                  f"sample cut to its effective size for autocorrelation, significant under p {P_SIGNIFICANT}",
                  [ref, *[r["point_id"] for r in rows[:10]]], unit="points that changed", points=rows[:20])


def _incident_of(conn, ref: str) -> str:
    kind = conn.execute("select kind from entity where fuzer_id = %s", (ref,)).fetchone()
    if kind and kind["kind"] == "incident":
        return ref
    if ref.startswith("upset:"):
        r = conn.execute("select fuzer_id from golden where field = 'upsets' and value @> %s::jsonb",
                         (f"[{int(ref.split(':')[1])}]",)).fetchone()
        if not r:
            raise CalcError("that upset isn't part of an incident yet")
        return r["fuzer_id"]
    r = conn.execute(
        """
        select a.src from edge a join golden g on g.fuzer_id = a.src and g.field = 'start'
        where a.rel = 'about' and a.dst = %s order by g.value #>> '{}' desc limit 1
        """, (ref,)).fetchone()
    if not r:
        raise CalcError("no incidents on that unit")
    return r["src"]


def similar_cases(conn, ref: str, limit: int = 8) -> dict:
    """Past incidents like this one, with what was done for each and whether it held."""
    from .graph import queries

    inc = _incident_of(conn, ref)
    sims = queries.similar_incidents(conn, inc, limit)
    out = []
    for s in sims:
        fixes = [dict(r) for r in conn.execute(
            """
            select distinct wo.src as id, l.label, max(g.value #>> '{}') filter (where g.field = 'description') as description,
                   max(g.value #>> '{}') filter (where g.field = 'activity_name') as activity,
                   max(g.value #>> '{}') filter (where g.field = 'finish') as finished
            from edge inc join edge wo on wo.dst = inc.dst and wo.rel = 'for_notification'
            left join entity_label l on l.fuzer_id = wo.src left join golden g on g.fuzer_id = wo.src
            where inc.src = %s and inc.rel = 'includes' group by wo.src, l.label
            """, (s["id"],))]
        unit = conn.execute("select dst from edge where rel = 'about' and src = %s", (s["id"],)).fetchone()
        again = None
        if unit and s.get("end"):
            end = s["end"][:10]
            again = conn.execute(
                """
                select g.value #>> '{}' as start from edge a join golden g on g.fuzer_id = a.src and g.field = 'start'
                where a.rel = 'about' and a.dst = %s and a.src <> %s and g.value #>> '{}' > %s and g.value #>> '{}' <= %s
                order by 1 limit 1
                """, (unit["dst"], s["id"], end,
                      (datetime.fromisoformat(end) + timedelta(days=90)).date().isoformat())).fetchone()
        out.append({**s, "unit": _label(conn, unit["dst"]) if unit else None, "fixes": fixes,
                    "came_back_within_90_days": again["start"] if again else None,
                    "held": None if not fixes else again is None})
    held = [c for c in out if c["held"]]
    return result(len(out), None, "repeat_fixes_failed" if any(c["held"] is False for c in out) else None,
                  "past incidents scored by same unit, same class, shared failure modes and event types; fixes are the "
                  "work orders raised for their notifications; a fix held if the unit had no incident within 90 days",
                  [inc, *[c["id"] for c in out]], unit="similar incidents", incident=inc, cases=out,
                  fixes_that_held=[f for c in held for f in c["fixes"]][:10])
