"""Metering and balances (P4.2, pack 8): how far a meter's reading can be trusted, from what the rest of the plant
says about the same flow. Everything here is read from the allocation network (flows_to), the meter's flow point and
the well tests upstream of it, so nothing knows which facility, which fluid or which meter it is looking at.

- `allocation_factor(ref)`: a meter's measured volume over the window against what the wells upstream of it offer at
  their latest tested rates (oil for a liquid meter; formation gas, from the GOR or the gas rate, for a gas meter;
  never lift gas, which goes round). Off one, either the tests are stale or the meter reads wrong, and every well's
  allocated volume carries the error. Flags `af_low` (below 0.90), `af_high` (above 1.10) and, inside that band,
  `af_shift` (moved by 0.05 or more since the window before).
- `meter_agreement(ref)`: a meter against the nearest other meter on the same stream (custody against check meter).
  Their ratio holds steady while both read true (shrinkage and fuel between them change slowly); a step in it is one
  of them drifting. Flags `meter_drift` when the last week's ratio is 2 % or more off its level over the month
  before. Two meters can't say which one moved: the result says so, and what would.

Days the meter reads under a third of its usual flow (a trip, a shut-in) are left out of both.
"""

from datetime import date, datetime, timedelta

import numpy as np

from .calcs import CalcError, _label, _r, read, result, window_of

AF_LOW, AF_HIGH = 0.90, 1.10  # allocation factor band
AF_SHIFT = 0.05  # change since the window before that is flagged inside the band
DRIFT_PCT = 2.0  # change in the ratio of two meters on one stream, %
IDLE_SHARE = 0.3  # a day under this share of the window's median flow is not counted
TEST_DAYS = 120  # a well test older than this doesn't count for the day's offer
BASELINE_DAYS = 30  # the level the meters' ratio is compared with


def _meter(conn, fid: str) -> dict:
    from .equipment import role_point

    p = role_point(conn, fid, "flow")
    if not p:
        raise CalcError("no flow point on that meter")
    return p


def _phase(p: dict) -> str:
    """gas or liquid, from the point's units (and, when they can't tell, its description)."""
    u, d = (p.get("uom") or "").lower().replace(" ", ""), (p.get("description") or "").lower()
    if u.startswith(("ksm3", "mmscf", "mscf", "knm3", "nm3")) or "gas" in d:
        return "gas"
    if u.startswith(("sm3", "m3", "bbl", "stb", "kbd", "bpd", "t/")):
        return "liquid"
    raise CalcError(f"can't tell whether that meter measures gas or liquid from its units ({p.get('uom')})")


def _per_day(p: dict) -> float:
    """Factor from the point's rate to a daily rate."""
    u = (p.get("uom") or "").lower()
    return 24.0 if u.endswith(("/h", "/hr")) else 1440.0 if u.endswith("/min") else 1.0


def _daily(conn, p: dict, start: datetime, end: datetime, src) -> dict[str, float]:
    """The meter's daily mean rate (per day), on the days it was flowing."""
    v = read({"v": p}, start, end, 60, src)["v"]
    by: dict[str, list[float]] = {}
    for t, x in v.items():
        if x is not None:
            by.setdefault(t[:10], []).append(x)
    days = {d: float(np.mean(xs)) * _per_day(p) for d, xs in by.items() if len(xs) >= 12}
    if not days:
        return {}
    usual = float(np.median(list(days.values())))
    return {d: x for d, x in days.items() if usual > 0 and x >= IDLE_SHARE * usual}


def _offer(conn, wells: list[str], days: list[str], phase: str) -> tuple[dict[str, float], list[str]]:
    """What the wells offer each day at their latest test within TEST_DAYS: oil, or formation gas (kSm3/d)."""
    if not days:
        return {}, []
    first = date.fromisoformat(days[0]) - timedelta(days=TEST_DAYS)
    rows = conn.execute(
        "select fuzer_id, record, at, quantity, value from measurement where fuzer_id = any(%s) and method = 'well test' "
        "and at >= %s and at < %s and quantity in ('oil_rate', 'gor', 'gas_rate') order by at",
        (wells, first, date.fromisoformat(days[-1]) + timedelta(days=1))).fetchall()
    tests: dict[str, dict[str, dict]] = {}  # well -> record -> {at, quantity: value}
    for r in rows:
        tests.setdefault(r["fuzer_id"], {}).setdefault(r["record"], {"at": r["at"]})[r["quantity"]] = r["value"]
    out, used = {}, set()
    for d in days:
        end, total = datetime.fromisoformat(d) + timedelta(days=1), 0.0
        for recs in tests.values():
            live = [(k, t) for k, t in recs.items() if end - timedelta(days=TEST_DAYS) < t["at"] < end]
            if not live:
                continue
            k, t = max(live, key=lambda kt: kt[1]["at"])
            used.add(k)
            oil = t.get("oil_rate") or 0.0
            total += oil if phase == "liquid" else oil * (t.get("gor") or 0.0) / 1000 + (t.get("gas_rate") or 0.0)
        out[d] = total
    return out, sorted(used)


def _factor(metered: dict[str, float], offered: dict[str, float]) -> tuple[float | None, float, float, int]:
    both = [d for d in metered if offered.get(d)]
    m, o = sum(metered[d] for d in both), sum(offered[d] for d in both)
    return (m / o if o else None), m, o, len(both)


def allocation_factor(conn, fid: str, days: int = 30, until: str | None = None, src=None) -> dict:
    """A meter's measured volume against what the wells upstream offer at their tested rates, now and the window
    before."""
    from ..graph import edges

    p = _meter(conn, fid)
    phase = _phase(p)
    start, end = window_of(conn, days, until)
    wells = sorted({r["fuzer_id"] for r in edges.trace(conn, fid, "flows_to", "up", end.date(), 12) if r["kind"] == "well"})
    if not wells:
        raise CalcError("no wells upstream of that meter on the allocation network")
    now = _daily(conn, p, start, end, src)
    offered, tests = _offer(conn, wells, sorted(now), phase)
    af, metered, offer, n = _factor(now, offered)
    if af is None:
        raise CalcError(f"no well test within {TEST_DAYS} days of the window for the wells upstream")
    before = _daily(conn, p, start - timedelta(days=days), start, src)
    prev_offered, _ = _offer(conn, wells, sorted(before), phase)
    prev, _, _, _ = _factor(before, prev_offered)
    flag = ("af_low" if af < AF_LOW else "af_high" if af > AF_HIGH
            else "af_shift" if prev is not None and abs(af - prev) >= AF_SHIFT else None)
    unit = "kSm3/d" if phase == "gas" else "Sm3/d"
    return result(_r(af), 1.0, flag,
                  f"measured {phase} over {n} flowing days against the latest test (within {TEST_DAYS} days) of each of "
                  f"{len(wells)} wells upstream on the allocation network ({'formation gas, lift gas left out' if phase == 'gas' else 'oil'})",
                  [p["id"], *tests[:20]], meter=_label(conn, fid), point=p["point"], phase=phase, unit=unit,
                  metered_per_day=_r(metered / n, 1), offered_per_day=_r(offer / n, 1), days=n, wells=len(wells),
                  previous=_r(prev), band=[AF_LOW, AF_HIGH], window=[start.isoformat(), end.isoformat()])


def _partner(conn, fid: str, when: date) -> tuple[str, int, str] | None:
    """The nearest other meter on the same stream, up or down the allocation network: (id, hops, direction)."""
    from ..graph import edges
    from .equipment import role_point

    near = []
    for way in ("up", "down"):
        for r in edges.trace(conn, fid, "flows_to", way, when, 4):
            if r["kind"] == "tag" and role_point(conn, r["fuzer_id"], "flow") and conn.execute(
                    "select 1 from golden where fuzer_id = %s and field = 'ec_type' and value #>> '{}' = 'meter'",
                    (r["fuzer_id"],)).fetchone():
                near.append((r["fuzer_id"], r["depth"], way))
    return min(near, key=lambda x: x[1]) if near else None


def meter_agreement(conn, fid: str, days: int = 7, until: str | None = None, src=None) -> dict:
    """A meter against the nearest other meter on the same stream: their ratio now against its level before."""
    p = _meter(conn, fid)
    start, end = window_of(conn, days, until)
    other = _partner(conn, fid, end.date())
    if other is None:
        raise CalcError("no other meter on the same stream of the allocation network to check it against")
    oid, hops, way = other
    q = _meter(conn, oid)
    if _phase(p) != _phase(q):
        raise CalcError("the nearest meter on the stream measures another phase")
    base_start = start - timedelta(days=BASELINE_DAYS)
    a, b = _daily(conn, p, base_start, end, src), _daily(conn, q, base_start, end, src)
    cut = start.date().isoformat()
    now = [d for d in a if d in b and d >= cut and b[d]]
    base = [d for d in a if d in b and d < cut and b[d]]
    if len(now) < 3 or len(base) < 7:
        raise CalcError("too few days with both meters flowing to compare them")
    ratio = float(np.mean([a[d] / b[d] for d in now]))
    level = float(np.median([a[d] / b[d] for d in base]))
    change = (ratio / level - 1) * 100
    flag = "meter_drift" if abs(change) >= DRIFT_PCT else None
    return result(_r(ratio, 4), _r(level, 4), flag,
                  f"daily ratio to {_label(conn, oid)} ({hops} step{'s' if hops != 1 else ''} {'upstream' if way == 'up' else 'downstream'} "
                  f"on the allocation network) over the last {len(now)} flowing days against its median over the "
                  f"{len(base)} days before",
                  [p["id"], q["id"]], meter=_label(conn, fid), against=_label(conn, oid), against_id=oid,
                  change_pct=_r(change, 2), drift_pct=DRIFT_PCT,
                  which=None if flag is None else "two meters can't say which one moved: a third meter on the stream, "
                  "a prover run or the meter's last calibration can",
                  window=[start.isoformat(), end.isoformat()])
