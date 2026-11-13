"""The daily gas balance (#369): the gas the facility handled against where it went (export, lift gas, flare, fuel
and vent), and what is left over, the closure gap. The gap is always shown: a balance that closes to the meters'
scatter says the meters agree; one that opens says a meter, or a term the report doesn't carry, is off.

    daily(conn, since, until)   per day: gas in, each way out, the gap in kSm3 and as a share of gas in, and the
                                stretches of days the gap stayed open

A term the daily report doesn't carry (no fuel or vent reported at all) isn't taken as zero: the gap then includes it
and the response says which term is missing. Evidence only, not an emissions inventory.
"""

from statistics import median

from ..graph import operations as ops

GAP_PCT = 2.0  # a day whose gap is more than this share of the gas handled is open
OUT = (("gas_export_ksm3", "export"), ("lift_gas_ksm3", "lift gas"), ("flare_ksm3", "flare"),
       ("fuel_gas_ksm3", "fuel"), ("vent_ksm3", "vent"))


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def daily(conn, since: str | None = None, until: str | None = None, days: int = 365) -> dict:
    """Each day in [since, until) (default the `days` to the latest daily report): gas in against gas out, and the
    closure gap with the stretches it stayed open."""
    since, until = ops._window(conn, since, until, days)
    reports = sorted((r["f"] for r in ops._rows(
        conn, "production_day", "f->>'report_date' >= %(s)s and f->>'report_date' < %(u)s", {"s": since, "u": until})),
        key=lambda f: f["report_date"])
    base = {"since": since, "until": until, "threshold_pct": GAP_PCT,
            "source": "daily report: gas handled, gas export, lift gas, flare, fuel gas and vent"}
    if not reports:
        return base | {"days": [], "episodes": [], "missing_terms": [], "verdict": "no_daily_report",
                       "summary": {"days": 0}, "note": "No daily report in the window."}
    missing = [name for key, name in OUT if all(_f(f.get(key)) is None for f in reports)]
    rows = []
    for f in reports:
        gas_in = _f(f.get("gas_ksm3"))
        out = {name: _f(f.get(key)) for key, name in OUT}
        if gas_in is None or gas_in <= 0:
            rows.append({"date": f["report_date"][:10], "gas_in_ksm3": gas_in, **{k: v for k, v in out.items()},
                         "gap_ksm3": None, "gap_pct": None, "open": None})
            continue
        gone = sum(v for v in out.values() if v is not None)
        gap = gas_in - gone
        pct = 100 * gap / gas_in
        rows.append({"date": f["report_date"][:10], "gas_in_ksm3": round(gas_in, 1),
                     **{k: (round(v, 1) if v is not None else None) for k, v in out.items()},
                     "gap_ksm3": round(gap, 1), "gap_pct": round(pct, 2), "open": abs(pct) > GAP_PCT,
                     "zero_terms": [k for k, v in out.items() if v == 0 and k not in ("vent",)]})
    episodes, run = [], []
    for r in rows + [{"open": False}]:
        if r.get("open"):
            run.append(r)
        elif run:
            zero = sorted({t for d in run for t in d["zero_terms"]})
            episodes.append({"start": run[0]["date"], "end": run[-1]["date"], "days": len(run),
                             "median_gap_pct": round(median(d["gap_pct"] for d in run), 2),
                             "gap_ksm3": round(sum(d["gap_ksm3"] for d in run), 1),
                             "direction": "unaccounted" if run[0]["gap_ksm3"] > 0 else "over_accounted",
                             "terms_reading_zero": zero})
            run = []
    said = [r for r in rows if r["gap_pct"] is not None]
    level = round(median(r["gap_pct"] for r in said), 2) if said else None
    opened = sum(bool(r["open"]) for r in said)
    verdict = ("unknown" if level is None else "open" if abs(level) > GAP_PCT
               else "closes_with_episodes" if episodes else "closes")
    note = (f"Gas in less export, lift gas, flare, fuel and vent; a day is open past {GAP_PCT:g} % of the gas handled. "
            + (f"The report carries no {', '.join(missing)}, so the gap includes it. " if missing else "")
            + "A stretch where a term reads zero while the gap opens points at that meter. Evidence only, not an "
              "emissions inventory.")
    return base | {"days": rows, "episodes": episodes, "missing_terms": missing, "verdict": verdict, "note": note,
                   "summary": {"days": len(rows), "days_said": len(said), "days_open": opened,
                               "median_gap_pct": level, "episodes": len(episodes),
                               "gas_in_ksm3": round(sum(r["gas_in_ksm3"] for r in said)),
                               "gap_ksm3": round(sum(r["gap_ksm3"] for r in said), 1)}}
