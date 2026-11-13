"""How detection and agents are performing (P7.4, #955): which detectors and skills earn their keep, and which need
tuning.

P7.4a, per detector. Each case is put down to the registered detector behind it, as the close-outs are
(`tuning.cases`, P6.3a), and counted there:

- **cases** raised, how many are closed out, and the confirmed, false-alarm and noise rates of those closed out;
- **hours to case**: the median hours from the start of what was found (the finding's `start`, else when the
  exception was first seen) to when its case opened;
- **value confirmed**: the value at stake (`value_usd`, #100) on the cases closed out as a real problem, where the case
  carries one, and how many of them carry one;
- **by origin**: how the confirmed cases were found, by a rule (deterministic), an agent (agentic) or another system
  (external), from the finding's own record; `unknown` when the case's exception carries no finding.

A detector earns its keep when what it raises is mostly confirmed. It **needs tuning** when at least `MIN_CLOSED` of
its cases are closed out and the noise rate (false alarms and duplicates) is `NOISY` or more. Too few close-outs say
nothing yet.

    detectors(conn)    per registered detector that raised a case, the most cases first; and the share of confirmed
                       cases by origin across them all
"""

from statistics import median

from ..core import detectors as registry
from . import tuning

MIN_CLOSED = 5  # close-outs before a detector's rates say anything
NOISY = 0.5  # the noise rate at which a detector needs tuning
ORIGINS = ("deterministic", "agentic", "external", "unknown")  # unknown: the case's finding names no origin


def _facts(conn, ids: list[int]) -> dict[int, dict]:
    """Per case: when what was found started, when the case opened, its value at stake and the finding's origin."""
    if not ids:
        return {}
    rows = conn.execute(
        """
        select r.item_id, i.created_at as opened,
               min(coalesce(nullif(r.evidence -> 'finding' ->> 'start', '')::timestamptz, r.first_seen)) as began,
               max(r.value_usd) as value_usd,
               (array_agg(r.evidence -> 'finding' ->> 'origin' order by r.id))[1] as origin
        from exception_record r join work_item i on i.id = r.item_id
        where r.item_id = any(%s)
        group by r.item_id, i.created_at
        """, (ids,)).fetchall()
    return {r["item_id"]: dict(r) for r in rows}


def _hours(a, b) -> float | None:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600


def verdict(t: dict) -> str:
    """`earning` (mostly confirmed), `needs_tuning` (noisy), `mixed`, or `too_few` close-outs to say."""
    if t["closed"] < MIN_CLOSED:
        return "too_few"
    if t["noise_rate"] >= NOISY:
        return "needs_tuning"
    return "earning" if t["confirmed_rate"] >= 0.5 else "mixed"


def detectors(conn) -> dict:
    """Per registered detector: its cases and close-outs, hours to case, the value it confirmed and how its confirmed
    cases were found; with each detector's title and kind, and the shares by origin over all confirmed cases."""
    cases = tuning.cases(conn)
    facts = _facts(conn, [c["item_id"] for c in cases])
    reg = registry.registry()
    by: dict[str, list[dict]] = {}
    for c in cases:
        by.setdefault(c["detector"], []).append({**c, **facts.get(c["item_id"], {})})
    rows, origins = [], dict.fromkeys(ORIGINS, 0)
    for name, cs in by.items():
        t = tuning.tally([c["disposition"] for c in cs])
        real = [c for c in cs if c["disposition"] == "real"]
        hours = [h for c in cs if (h := _hours(c.get("began"), c.get("opened"))) is not None and h >= 0]
        valued = [float(c["value_usd"]) for c in real if c.get("value_usd") is not None]
        found = dict.fromkeys(ORIGINS, 0)
        for c in real:
            o = c.get("origin") if c.get("origin") in ORIGINS else "unknown"
            found[o] += 1
            origins[o] += 1
        d = reg.get(name)
        rows.append({"detector": name, "title": d.title if d else name, "kind": d.kind if d else None,
                     "kind_label": registry.KINDS[d.kind] if d else None, **t,
                     "hours_to_case": round(median(hours), 1) if hours else None,
                     "value_confirmed_usd": round(sum(valued), 2) if valued else None, "valued": len(valued),
                     "by_origin": found, "verdict": verdict(t),
                     "item_ids": sorted(c["item_id"] for c in cs)})
    rows.sort(key=lambda r: (-r["cases"], r["detector"]))
    total = sum(origins.values())
    return {"detectors": rows, "confirmed": total,
            "by_origin": {o: {"cases": n, "share": round(n / total, 3) if total else None} for o, n in origins.items()},
            "min_closed": MIN_CLOSED, "noisy": NOISY}
