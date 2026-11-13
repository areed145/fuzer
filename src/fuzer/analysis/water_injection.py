"""Water injection screening (#365): is the field putting back what it takes out, and is each injector still taking
water the way it did?

    voidage(conn, since, until)   per month: water injected against the reservoir volume produced, and their ratio
    hall(conn, since, until)      per injector: the Hall plot (cumulative pressure x days against cumulative water)
                                  and whether its slope has bent since injection settled

Voidage is reservoir volume: oil times its formation volume factor, plus produced water, plus free gas (the producing
GOR above the solution GOR). Allocated oil comes from the daily report; water cut and GOR from each producing well's
latest test within TEST_DAYS, weighted by its tested oil. The formation volume factors and solution GOR are recorded
measurements (a PVT study): without them the ratio is unknown, never assumed. A field or window with no injection
says so instead of a ratio of zero.

The Hall plot uses wellhead injection pressure, not bottomhole, so its slope reads skin and plugging (rising) or
fracturing and channelling (falling) only against the same injector's own earlier slope. Screening only: what to do
about an injector, or the injection target, stays with the engineers.
"""

from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from ..graph import operations as ops
from .metering import TEST_DAYS

VRR_LOW = 0.9  # latest months' ratio below this: the field is under-replacing what it produces
VRR_HIGH = 1.1  # above this: injecting more than it produces
VRR_MONTHS = 3  # months at the end read for the verdict
HALL_MONTHS = 3  # months at each end of an injector's history compared for its slope
HALL_RISE = 1.25  # latest slope over the earlier one above this: rising (plugging or skin)
HALL_FALL = 0.8  # below this: falling (fracturing or channelling)
PVT = ("oil_fvf", "water_fvf", "gas_fvf", "solution_gor")
INJECTION = "monthly injection"  # the method injection records carry


def _months(conn, since, until, days):
    given = since
    since, until = ops._window(conn, since, until, days)
    if not given and since[8:10] != "01":  # whole months
        since = (date.fromisoformat(since[:8] + "01") + timedelta(days=32)).replace(day=1).isoformat()
    return since, until


def _pvt(conn, until: str) -> tuple[dict, dict]:
    """The latest recorded value of each fluid property before `until`, and where each came from."""
    got, src = {}, {}
    for r in conn.execute(
            "select distinct on (quantity) quantity, value, unit, method, record, at from measurement "
            "where quantity = any(%s) and at < %s order by quantity, at desc", (list(PVT), until)):
        got[r["quantity"]] = float(r["value"])
        src[r["quantity"]] = {"value": float(r["value"]), "unit": r["unit"], "method": r["method"],
                              "record": r["record"], "at": r["at"].date().isoformat()}
    return got, src


def _injected(conn, since: str, until: str) -> dict[str, dict[str, dict]]:
    """Injector -> month -> {water_sm3, days, whp_barg} from the monthly injection records."""
    out: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(dict))
    names = {}
    for r in conn.execute(
            "select m.fuzer_id, l.label, m.at, m.quantity, m.value from measurement m "
            "left join entity_label l using (fuzer_id) where m.method = %s and m.at >= %s and m.at < %s order by m.at",
            (INJECTION, since, until)):
        names[r["fuzer_id"]] = r["label"] or r["fuzer_id"]
        out[r["fuzer_id"]][r["at"].date().isoformat()[:7]][r["quantity"]] = float(r["value"])
    return {names[k]: v for k, v in out.items()}


def voidage(conn, since: str | None = None, until: str | None = None, days: int = 730) -> dict:
    """Per month in [since, until) (default the `days` to the latest daily report): water injected against the reservoir
    volume the field produced, with the ratio and its running total."""
    since, until = _months(conn, since, until, days)
    booked = {r["f"]["report_date"][:10]: r["f"] for r in ops._rows(
        conn, "production_day", "f->>'report_date' >= %(s)s and f->>'report_date' < %(u)s", {"s": since, "u": until})}
    pvt, pvt_src = _pvt(conn, until)
    missing = [q for q in PVT if q not in pvt]
    base = {"since": since, "until": until, "pvt": pvt_src, "missing_pvt": missing,
            "thresholds": {"vrr_low": VRR_LOW, "vrr_high": VRR_HIGH, "months": VRR_MONTHS, "test_days": TEST_DAYS},
            "source": "daily report oil and water injection, monthly injection records, well tests (water cut, GOR), "
                      "PVT measurements (formation volume factors, solution GOR)"}
    if not booked:
        return base | {"months": [], "verdict": "no_daily_report", "summary": {"months": 0},
                       "note": "No daily report in the window, so nothing was produced to replace."}
    per_well = _injected(conn, since, until)
    by_month_inj: dict[str, float] = defaultdict(float)
    for months in per_well.values():
        for m, v in months.items():
            by_month_inj[m] += v.get("water_injected", 0.0)
    oil: dict[str, float] = defaultdict(float)
    daily_inj: dict[str, float] = defaultdict(float)
    for day, f in booked.items():
        oil[day[:7]] += ops._num(f.get("oil_sm3"))
        daily_inj[day[:7]] += ops._num(f.get("water_inj_sm3"))
    tests: dict[str, list[tuple]] = defaultdict(list)  # well -> [(at, quantity, value)]
    for r in conn.execute(
            "select fuzer_id, at, quantity, value from measurement where method = 'well test' "
            "and quantity = any(%s) and at >= %s and at < %s order by at",
            (["oil_rate", "water_cut", "gor"], date.fromisoformat(since) - timedelta(days=TEST_DAYS), until)):
        tests[r["fuzer_id"]].append((r["at"], r["quantity"], float(r["value"])))
    rows, cum_inj, cum_void = [], 0.0, 0.0
    for m in sorted(oil):
        end = (date.fromisoformat(m + "-01") + timedelta(days=32)).replace(day=1)
        q_oil = q_water = q_gas = 0.0
        for ts in tests.values():
            live: dict[str, float] = {}
            for at, q, v in ts:  # each quantity's latest value from tests within TEST_DAYS of the month's end
                if end - timedelta(days=TEST_DAYS) < at.date() < end:
                    live[q] = v
            qo, wc = live.get("oil_rate", 0.0), live.get("water_cut")
            if qo <= 0 or wc is None or wc >= 100:
                continue
            q_oil += qo
            q_water += qo * wc / (100 - wc)
            if "gor" in live and "solution_gor" in pvt:
                q_gas += qo * max(0.0, live["gor"] - pvt["solution_gor"])
        injected = by_month_inj.get(m) or daily_inj.get(m, 0.0)
        water = oil[m] * q_water / q_oil if q_oil else None
        gas = oil[m] * q_gas / q_oil if q_oil else None
        void = (oil[m] * pvt["oil_fvf"] + water * pvt["water_fvf"] + gas * pvt["gas_fvf"]
                if not missing and water is not None else None)
        inj_r = injected * pvt["water_fvf"] if "water_fvf" in pvt else None
        if void and inj_r is not None:
            cum_inj, cum_void = cum_inj + inj_r, cum_void + void
        status = "no_injection" if injected <= 0 else "unknown" if not void or inj_r is None else "injecting"
        rows.append({"month": m, "oil_sm3": round(oil[m]), "water_sm3": round(water) if water is not None else None,
                     "free_gas_sm3": round(gas) if gas is not None else None, "injected_sm3": round(injected),
                     "voidage_rm3": round(void) if void else None, "injected_rm3": round(inj_r) if inj_r is not None else None,
                     "vrr": round(inj_r / void, 3) if status == "injecting" else None,
                     "cumulative_vrr": round(cum_inj / cum_void, 3) if status == "injecting" and cum_void else None,
                     "injectors": sorted(w for w, months in per_well.items() if months.get(m, {}).get("water_injected")),
                     "status": status})
    latest = [r["vrr"] for r in rows if r["vrr"] is not None][-VRR_MONTHS:]
    level = round(median(latest), 3) if latest else None
    if not any(r["injected_sm3"] for r in rows):
        verdict, note = "no_injection", "No water injection recorded in the window: there is no ratio to read, not a ratio of zero."
    elif missing:
        verdict, note = "unknown", f"No recorded {', '.join(missing)}, so reservoir volumes can't be worked out and the ratio is unknown."
    elif level is None:
        verdict, note = "unknown", "No month with both injection and a producing well test to set water and gas against."
    else:
        verdict = "under_replacing" if level < VRR_LOW else "over_injecting" if level > VRR_HIGH else "balanced"
        note = (f"Injection over produced reservoir volume, the median of the latest {len(latest)} months "
                f"with injection. Water cut and GOR come from each well's latest test within {TEST_DAYS} days. "
                "Screening only: the injection target stays with the engineers.")
    return base | {"months": rows, "verdict": verdict, "note": note,
                   "summary": {"months": len(rows), "months_injecting": sum(r["status"] == "injecting" for r in rows),
                               "latest_vrr": level, "cumulative_vrr": next(
                                   (r["cumulative_vrr"] for r in reversed(rows) if r["cumulative_vrr"] is not None), None),
                               "injectors": sorted(per_well)}}


def _slope(points: list[tuple[float, float]]) -> float | None:
    """Least squares slope of y on x."""
    if len(points) < 2:
        return None
    mx, my = sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points)
    sxx = sum((x - mx) ** 2 for x, _ in points)
    return sum((x - mx) * (y - my) for x, y in points) / sxx if sxx else None


def hall(conn, since: str | None = None, until: str | None = None, days: int = 730, well: str | None = None) -> dict:
    """Each injector's Hall plot in the window (or one injector's): cumulative wellhead pressure x days on, against
    cumulative water, and its latest slope against its earlier one."""
    since, until = _months(conn, since, until, days)
    per_well = _injected(conn, since, until)
    if well:
        per_well = {k: v for k, v in per_well.items() if k.lower() == well.lower()}
    out = []
    for name, months in sorted(per_well.items()):
        pts, cw, cpt = [], 0.0, 0.0
        for m in sorted(months):
            v = months[m]
            w, d, p = v.get("water_injected", 0.0), v.get("injection_days", 0.0), v.get("injection_pressure")
            if w <= 0 or p is None or d <= 0:
                continue
            cw, cpt = cw + w, cpt + p * d
            pts.append({"month": m, "water_sm3": round(w), "days": d, "whp_barg": p,
                        "cumulative_water_sm3": round(cw), "cumulative_bar_days": round(cpt, 1)})
        xy = [(p["cumulative_water_sm3"], p["cumulative_bar_days"]) for p in pts]
        early = _slope(xy[:len(xy) // 2]) if len(xy) >= 2 * HALL_MONTHS else None
        late = _slope(xy[-HALL_MONTHS - 1:]) if len(xy) >= 2 * HALL_MONTHS else None
        ratio = round(late / early, 2) if early and late else None
        verdict = ("too_few_months" if ratio is None else "rising" if ratio > HALL_RISE
                   else "falling" if ratio < HALL_FALL else "steady")
        out.append({"well": name, "points": pts, "months": len(pts), "early_slope": early and round(early, 5),
                    "late_slope": late and round(late, 5), "slope_ratio": ratio, "verdict": verdict,
                    "reads": {"rising": "harder to inject: plugging or skin build-up near the well",
                              "falling": "easier to inject: a fracture opening or water channelling",
                              "steady": "taking water as it did", "too_few_months":
                              f"fewer than {2 * HALL_MONTHS} months of injection to compare"}[verdict]})
    return {"since": since, "until": until, "injectors": out,
            "verdict": "no_injection" if not out else "flagged" if any(i["verdict"] in ("rising", "falling") for i in out)
            else "steady",
            "thresholds": {"months": HALL_MONTHS, "rise": HALL_RISE, "fall": HALL_FALL},
            "source": "monthly injection records (water, days on, wellhead pressure)",
            "note": ("No water injection recorded in the window." if not out else
                     f"Slope of the latest {HALL_MONTHS} months over the first half of each injector's history. "
                     "Wellhead pressure, not bottomhole, so read each injector against itself. Screening only: what "
                     "to do about an injector stays with the engineers.")}
