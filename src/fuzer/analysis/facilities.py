"""Facility loading in each item's own terms (#515).

`flow_capacity` and `capacity_bounds` already turn every rating into what the whole facility could carry as oil,
and say which stage binds. What they don't say is how hard one vessel is working against its own nameplate as the
fluids change: a separator rated for liquid fills up with water while the oil stays flat.

- `vessel_loading(ref)`: a separator's or scrubber's throughput in the units of its standing rating (liquid,
  oil or gas, from the design basis, capacity statement or MOC register, newest grade first, as `capacity_bounds`
  reads them), from the latest test of every well upstream of it on the flow network that day, against that
  rating, and the same 90 days earlier. The throughput is what the wells offer at their tested rates, so a vessel
  over 100 % is holding production back (or running above its rating): flags `overloaded` at 100 % or more and
  `near_capacity` at 90 %.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from .calcs import CalcError, _label, _r, result

BBL = 6.2898  # bbl per Sm3
MMSCF = 28.3168  # kSm3 per MMscf
NEAR = 90.0
TEST_DAYS = 120  # a well test older than this doesn't count for the day's throughput


def _ratings(conn, fid: str, day: date) -> dict | None:
    from .capacity_bounds import RATED, standing

    rows = conn.execute(
        "select quantity, value, unit, at, method, record from measurement where fuzer_id = %s and quantity = any(%s)",
        (fid, sorted(RATED))).fetchall()
    rated = standing([{"id": fid, "quantity": r["quantity"], "at": r["at"].date(), "value": r["value"],
                       "unit": r["unit"], "method": r["method"], "record": r["record"]} for r in rows
                      if r["method"] in ("design", "statement", "moc", "tested")], day)
    return {q: row for (_, q), row in rated.items()} or None


def _through(conn, fid: str, day: date) -> tuple[dict, list[str], list[str]]:
    """Oil, water and gas (Sm3/d, kSm3/d) from the latest test, within TEST_DAYS, of each well upstream on `day`."""
    from ..graph import edges

    wells = [r["fuzer_id"] for r in edges.trace(conn, fid, "flows_to", "up", day, 12) if r["kind"] == "well"]
    if not wells:
        return {}, [], []
    rows = conn.execute(
        """
        select distinct on (fuzer_id, quantity) fuzer_id, quantity, value, record, at from measurement
        where fuzer_id = any(%s) and method = 'well test' and at <= %s and at > %s
          and quantity in ('oil_rate', 'water_cut', 'gor', 'lift_gas_rate', 'gas_rate')
        order by fuzer_id, quantity, at desc
        """, (wells, datetime(day.year, day.month, day.day) + timedelta(days=1),
              datetime(day.year, day.month, day.day) - timedelta(days=TEST_DAYS))).fetchall()
    per: dict[str, dict] = defaultdict(dict)
    records = {}
    for r in rows:
        per[r["fuzer_id"]][r["quantity"]] = r["value"]
        records[r["fuzer_id"]] = r["record"]
    total = {"oil": 0.0, "water": 0.0, "gas": 0.0}
    for t in per.values():
        oil, wc = t.get("oil_rate") or 0.0, min(t.get("water_cut") or 0.0, 99.0)
        total["oil"] += oil
        total["water"] += oil * wc / (100 - wc)
        total["gas"] += oil * (t.get("gor") or 0.0) / 1000 + (t.get("lift_gas_rate") or 0.0) + (t.get("gas_rate") or 0.0)
    return total, wells, list(records.values())


def _in_units(quantity: str, unit: str, flow: dict) -> float | None:
    """The throughput in the rating's own quantity and unit."""
    u = (unit or "").lower()
    if quantity in ("liquid_capacity", "oil_capacity"):
        sm3 = flow["oil"] + (flow["water"] if quantity == "liquid_capacity" else 0.0)
        return sm3 * BBL if u.startswith("bbl") else sm3 if u.startswith("sm3") else None
    if quantity in ("gas_capacity", "export_gas_capacity"):
        return flow["gas"] / MMSCF if u.startswith("mmscf") else flow["gas"] if u.startswith("ksm3") else None
    return None


def vessel_loading(conn, fid: str, until: str | None = None, back_days: int = 90) -> dict:
    from ..graph import operations

    day = date.fromisoformat((until or operations.as_of(conn))[:10])
    rated = _ratings(conn, fid, day)
    if not rated:
        raise CalcError("no liquid, oil or gas rating on record for that item (design basis, capacity statement or MOC)")
    flow, wells, records = _through(conn, fid, day)
    if not wells:
        raise CalcError("no wells upstream of that item on the flow network")
    loads = []
    for q, row in rated.items():
        now = _in_units(q, row["unit"], flow)
        if now is None:
            continue
        before_flow, _, _ = _through(conn, fid, day - timedelta(days=back_days))
        before = _in_units(q, row["unit"], before_flow) if before_flow else None
        loads.append({"quantity": q, "rating": row["value"], "unit": row["unit"], "grade": row["method"],
                      "rated_since": row["at"].isoformat(), "record": row["record"], "throughput": _r(now, 1),
                      "loading_pct": _r(now / row["value"] * 100, 1),
                      "loading_pct_before": _r(before / row["value"] * 100, 1) if before is not None else None})
    if not loads:
        raise CalcError("the item's rating is in a unit Fuzer can't compare with well tests")
    worst = max(loads, key=lambda x: x["loading_pct"])
    flag = "overloaded" if worst["loading_pct"] >= 100 else "near_capacity" if worst["loading_pct"] >= NEAR else None
    cut = flow["water"] / (flow["oil"] + flow["water"]) * 100 if flow["oil"] + flow["water"] else None
    return result(worst["throughput"], worst["rating"], flag,
                  f"{worst['quantity'].replace('_', ' ')} through {_label(conn, fid)}: the latest test (within "
                  f"{TEST_DAYS} days) of each of the {len(wells)} wells upstream on the flow network on {day}, against "
                  f"its standing {worst['grade']} rating of {worst['rating']:g} {worst['unit']} (since "
                  f"{worst['rated_since']}); the same {back_days} days earlier. What the wells offer at their tested rates, so "
                  f"over 100 % the item holds production back or runs above its rating. Near capacity at {NEAR:g} %",
                  [fid, worst["record"], *records[:3]], unit=worst["unit"], at=day.isoformat(),
                  loading_pct=worst["loading_pct"], loading_pct_before=worst["loading_pct_before"], loads=loads,
                  wells_upstream=len(wells), water_cut_pct=_r(cut, 1), oil_sm3d=_r(flow["oil"], 1),
                  water_sm3d=_r(flow["water"], 1), gas_ksm3d=_r(flow["gas"], 1))
