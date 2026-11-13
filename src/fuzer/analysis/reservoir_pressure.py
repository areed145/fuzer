"""Reservoir pressure from pressure surveys (#364): each surveyed well's productivity index, and a one-tank material
balance for the field. Both are estimates: every result returns the assumptions it rests on.

    productivity(conn, well=None)   per well: each flowing survey's rate over its drawdown from the static pressure
                                    surveyed just before, and whether the index has fallen since the first
    material_balance(conn, ...)     static pressures against the reservoir volume produced net of water injected:
                                    the pressure lost per reservoir m3, the compressible volume it implies, and the
                                    decline at the latest months' net voidage

The material balance reuses the voidage of water_injection.voidage (allocated oil with its water and free gas at
reservoir conditions from the recorded PVT, less injected water), so it is unknown where that is. It fits one slope
with each well's own level, since wells sit at different pressures in the same tank.
"""

from collections import defaultdict
from datetime import date, timedelta

from . import water_injection

PAIR_DAYS = 60  # a flowing survey pairs with the static survey on the same well at most this many days before it
PI_DROP = 0.75  # latest productivity index under this share of the first: fallen (damage, scale, or depletion)
MIN_SURVEYS = 3  # static surveys a material balance needs, over at least two dates
STATIC, FLOWING = "static_bottomhole_pressure", "flowing_bottomhole_pressure"

PI_ASSUMPTIONS = [
    "Straight-line inflow above the bubble point: productivity index = oil rate / (static pressure - flowing pressure).",
    f"The static pressure is the well's own static survey within {PAIR_DAYS} days before the flowing survey.",
    "Pressures are at the field datum as the survey reported them; the rate is the one the survey recorded.",
    "A falling index is a lead for damage, scale or a changing drive, not a diagnosis.",
]
MB_ASSUMPTIONS = [
    "One tank: every surveyed well sees the same reservoir; each keeps its own level, sharing one slope.",
    "No aquifer influx and no gas cap; the oil stays above its bubble point, so pressure falls in a straight line "
    "with the reservoir volume taken out.",
    "Voidage is allocated oil with its water and free gas at reservoir conditions, from the recorded PVT and each "
    "well's latest test, less water injected; gas injection is not counted.",
    "Formation volume factors are constant over the window.",
    "The slope is pressure per reservoir m3 net of injection; its inverse is the compressible volume (pore volume "
    "times total compressibility), not the oil in place.",
]


def _surveys(conn, quantity: str, well: str | None = None) -> dict[str, list[tuple[date, float, str]]]:
    rows = conn.execute(
        "select l.label, m.fuzer_id, m.at, m.value, m.record from measurement m left join entity_label l using (fuzer_id) "
        "where m.quantity = %s order by m.at", (quantity,)).fetchall()
    out: dict[str, list] = defaultdict(list)
    for r in rows:
        name = r["label"] or r["fuzer_id"]
        if well and name.lower() != well.lower():
            continue
        out[name].append((r["at"].date(), float(r["value"]), r["record"]))
    return out


def productivity(conn, well: str | None = None) -> dict:
    """Each surveyed well's productivity index over its flowing surveys, and whether it has fallen."""
    static, flowing = _surveys(conn, STATIC, well), _surveys(conn, FLOWING, well)
    rates = {(r["fuzer_id"], r["at"].date()): float(r["value"]) for r in conn.execute(
        "select fuzer_id, at, value from measurement where quantity = 'survey_oil_rate'")}
    ids = {r["label"]: r["fuzer_id"] for r in conn.execute(
        "select l.label, e.fuzer_id from entity e join entity_label l using (fuzer_id) where e.kind = 'well'")}
    wells = []
    for name in sorted(set(static) | set(flowing)):
        points = []
        for day, pwf, record in flowing.get(name, []):
            before = [(d, p) for d, p, _ in static.get(name, []) if day - timedelta(days=PAIR_DAYS) <= d <= day]
            q = rates.get((ids.get(name), day))
            if not before or q is None:
                points.append({"date": day.isoformat(), "flowing_barg": pwf, "record": record, "pi": None,
                               "why": "no static survey just before" if not before else "no rate on the survey"})
                continue
            sd, ps = before[-1]
            dd = ps - pwf
            points.append({"date": day.isoformat(), "static_barg": ps, "static_date": sd.isoformat(), "flowing_barg": pwf,
                           "oil_sm3d": q, "drawdown_bar": round(dd, 1), "record": record,
                           "pi": round(q / dd, 2) if dd > 0 else None,
                           **({"why": "no drawdown"} if dd <= 0 else {})})
        said = [p for p in points if p["pi"]]
        ratio = round(said[-1]["pi"] / said[0]["pi"], 2) if len(said) >= 2 else None
        verdict = "too_few_surveys" if ratio is None else "fallen" if ratio < PI_DROP else "holding"
        wells.append({"well": name, "surveys": points, "first_pi": said[0]["pi"] if said else None,
                      "latest_pi": said[-1]["pi"] if said else None, "ratio": ratio, "verdict": verdict})
    return {"wells": wells, "unit": "Sm3/d per bar", "threshold": PI_DROP, "pair_days": PAIR_DAYS,
            "verdict": "no_surveys" if not wells else "flagged" if any(w["verdict"] == "fallen" for w in wells)
            else "holding", "assumptions": PI_ASSUMPTIONS,
            "source": "pressure surveys (static and flowing bottomhole pressure, survey rate)",
            "note": ("No pressure survey recorded." if not wells else
                     f"Index under {PI_DROP:g} times its first value is flagged fallen. An estimate: see assumptions.")}


def _fit(points: list[tuple[str, float, float]]) -> tuple[float, float] | None:
    """Slope of pressure on cumulative net voidage, each well with its own level; and r squared."""
    by: dict[str, list] = defaultdict(list)
    for w, v, p in points:
        by[w].append((v, p))
    xs, ys = [], []
    for pts in by.values():
        if len(pts) < 2:
            continue
        mv, mp = sum(v for v, _ in pts) / len(pts), sum(p for _, p in pts) / len(pts)
        xs += [v - mv for v, _ in pts]
        ys += [p - mp for _, p in pts]
    sxx = sum(x * x for x in xs)
    if len(xs) < MIN_SURVEYS or not sxx:
        return None
    slope = sum(x * y for x, y in zip(xs, ys)) / sxx
    sst = sum(y * y for y in ys)
    sse = sum((y - slope * x) ** 2 for x, y in zip(xs, ys))
    return slope, (1 - sse / sst) if sst else 0.0


def material_balance(conn, since: str | None = None, until: str | None = None, days: int = 730) -> dict:
    """Static survey pressures against cumulative reservoir voidage net of injection in the window."""
    v = water_injection.voidage(conn, since, until, days)
    base = {"since": v["since"], "until": v["until"], "assumptions": MB_ASSUMPTIONS, "pvt": v["pvt"],
            "min_surveys": MIN_SURVEYS,
            "source": "pressure surveys (static), daily report oil, monthly injection, well tests, PVT measurements"}
    months = [m for m in v["months"] if m["voidage_rm3"] is not None]
    if v["missing_pvt"] or not months:
        return base | {"verdict": "unknown", "surveys": [], "note": (
            f"No recorded {', '.join(v['missing_pvt'])}, so the reservoir volume taken out is unknown."
            if v["missing_pvt"] else "No month with produced reservoir volume in the window.")}
    cum, start = {}, 0.0  # cumulative net voidage at the start of each month
    for m in months:
        cum[m["month"]] = (start, m["voidage_rm3"] - (m["injected_rm3"] or 0))
        start += cum[m["month"]][1]

    def at(day: date) -> float | None:
        key = day.isoformat()[:7]
        if key not in cum:
            return None
        s, month_net = cum[key]
        first = date.fromisoformat(key + "-01")
        length = ((first + timedelta(days=32)).replace(day=1) - first).days
        return s + month_net * (day - first).days / length

    pts, rows = [], []
    for name, surveys in _surveys(conn, STATIC).items():
        for day, p, record in surveys:
            nv = at(day)
            if nv is None:
                continue
            pts.append((name, nv, p))
            rows.append({"well": name, "date": day.isoformat(), "static_barg": p,
                         "cumulative_net_voidage_rm3": round(nv), "record": record})
    fit = _fit(pts)
    if fit is None or fit[0] >= 0:
        return base | {"verdict": "insufficient", "surveys": rows, "note": (
            f"Fewer than {MIN_SURVEYS} static surveys on wells surveyed twice in the window"
            if fit is None else "Pressure doesn't fall with net voidage here, so a one-tank balance doesn't hold.")}
    slope, r2 = fit
    latest = months[-water_injection.VRR_MONTHS:]
    net_rate = sum(m["voidage_rm3"] - (m["injected_rm3"] or 0) for m in latest) / len(latest) * 12
    return base | {
        "verdict": "fitted", "surveys": rows,
        "bar_per_million_rm3": round(-slope * 1e6, 3), "compressible_volume_rm3_per_bar": round(-1 / slope),
        "r2": round(r2, 3), "wells": len({r["well"] for r in rows}),
        "decline_bar_per_year": round(-slope * net_rate, 1),
        "net_voidage_rm3_per_year": round(net_rate),
        "note": (f"Pressure falls {round(-slope * 1e6, 2)} bar per million reservoir m3 taken out net of injection "
                 f"(r² {round(r2, 2)}, {len(rows)} static surveys); at the latest {len(latest)} months' net voidage "
                 f"that is {round(-slope * net_rate, 1)} bar a year. An estimate: see assumptions.")}
