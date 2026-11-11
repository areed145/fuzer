"""Machinery protection and auxiliaries: the channels a machinery engineer reads before the vibration trend.

- `protection(ref)`: the API 670 protection channels on a machine: shaft vibration at the proximity probes (um
  pk-pk), the rotor's axial (thrust) position (mm), and the journal and thrust bearing temperatures, each against its
  alarm and trip settings, with its 30-day trend and the date it reaches them at that rate. Flags `thrust_wear` (axial
  position or thrust pad temperature past its alarm: the thrust bearing is going, and a thrust failure wrecks the
  rotor), `bearing_hot` (a journal bearing past its alarm), `shaft_vibration_high` (shaft vibration past its alarm),
  `protection_rising` (a channel on course for its alarm within 30 days).
- `seal_gas(ref)`: a dry gas seal's primary vent flow (API 692) against its own clean baseline and its alarm: a
  primary seal that passes more and more gas is on its way out. Flags `seal_leak` (past the alarm: plan the seal
  change now, the secondary seal is all that's left) and `seal_rising` (doubled and climbing).
- `lube_oil(ref)`: the lube oil system (API 614): header pressure against its low alarm and trip, supply temperature
  against its high alarm, each trended. Flags `lube_pressure_low` (below the low alarm: change over the filter or the
  pump before it trips), `lube_pressure_falling` (on course for the low alarm within 30 days) and `lube_oil_hot`.

Every value is read while the machine runs (its own or its driver's speed), so a stop doesn't read as a recovery.
"""

from datetime import timedelta

import numpy as np

from .calcs import CalcError, _label, read, result, window_of
from .condition import crossed, daily, eta, points_like, running, settings

SOON_DAYS = 30  # a channel reaching its alarm within this is flagged as rising

CHANNELS = [  # (name, description pattern, unit, which side its limits are on)
    ("shaft vibration", r"shaft vibration", "um", "hi"),
    ("axial position", r"axial position|thrust position", "mm", "hi"),
    ("thrust bearing temperature", r"thrust bearing temp", "degC", "hi"),
    ("journal bearing temperature", r"journal bearing temp|^(?!.*thrust).*bearing temp", "degC", "hi"),
]


def _tag(conn, fid: str) -> str:
    from .rotating import _tag as tag

    return tag(conn, fid)


def _within(day: str | None, end, days: int) -> bool:
    return bool(day) and day != "now" and day <= (end + timedelta(days=days)).date().isoformat()


def _channel(name, p, series, keep, lim, side, end) -> dict | None:
    days, ys = daily(series, keep)
    if len(days) < 3:
        return None
    now = float(np.median(ys[-3:]))
    alarm, trip = (lim.get("hi"), lim.get("hihi")) if side == "hi" else (lim.get("lo"), lim.get("lolo"))
    to_alarm, to_trip = eta(days, ys, alarm, side), eta(days, ys, trip, side)
    return {"channel": name, "point": p["point"], "id": p["id"], "now": round(now, 2), "unit": p["uom"],
            "alarm": alarm, "trip": trip, "state": crossed(now, lim, side) or "normal",
            "per_week": round(to_alarm["per_day"] * 7, 3), "trend_r2": to_alarm["r2"],
            "reaches_alarm": to_alarm["reaches"], "reaches_trip": to_trip["reaches"],
            "trip_range": [to_trip["early"], to_trip["late"]] if to_trip["early"] else None,
            "data_from": days[0], "data_to": days[-1], "readings": len(series),
            "daily": [{"day": d, "value": round(float(y), 2)} for d, y in zip(days, ys)][-60:]}


def protection(conn, fid: str, days: int = 60, until: str | None = None, src=None) -> dict:
    """The API 670 protection channels on a machine against their alarm and trip settings, with trends."""
    tag = _tag(conn, fid)
    start, end = window_of(conn, days, until)
    found = []
    taken: set[str] = set()
    for name, pattern, uom, side in CHANNELS:
        for p in points_like(conn, tag, pattern):
            if p["id"] not in taken and (p["uom"] or "").lower() == uom.lower():
                taken.add(p["id"])
                found.append((name, p, side))
    if not found:
        raise CalcError("no protection channels (shaft vibration, axial position, bearing temperatures) on that machine")
    keep = running(conn, tag, start, end, 60, src)
    vals = read({p["id"]: p for _, p, _ in found}, start, end, 60, src)
    lims = settings(conn, [p["id"] for _, p, _ in found], end)
    channels = [c for name, p, side in found
                if (c := _channel(name, p, vals[p["id"]], keep, lims.get(p["id"], {}), side, end))]
    if not channels:
        raise CalcError("the machine hardly ran in that window, so its protection channels say nothing")
    flags = []
    for c in channels:
        past = c["state"] in ("alarm", "trip")
        if past and c["channel"] in ("axial position", "thrust bearing temperature"):
            flags.append("thrust_wear")
        elif past and c["channel"] == "journal bearing temperature":
            flags.append("bearing_hot")
        elif past and c["channel"] == "shaft vibration":
            flags.append("shaft_vibration_high")
        elif _within(c["reaches_alarm"], end, SOON_DAYS):
            flags.append("protection_rising")
    order = ["thrust_wear", "bearing_hot", "shaft_vibration_high", "protection_rising"]
    flag = min(flags, key=order.index) if flags else None
    rank = {"trip": 0, "alarm": 1, "normal": 2}
    worst = min(channels, key=lambda c: (rank[c["state"]], c["reaches_alarm"] or "9999",
                                         -(c["now"] / c["alarm"] if c["alarm"] else 0)))
    return result(worst["now"], worst["alarm"], flag,
                  "the API 670 protection channels (shaft vibration at the proximity probes, axial thrust position, "
                  "journal and thrust bearing temperatures), daily medians of hourly readings while the machine runs, "
                  "each against its alarm and trip settings (DCS / AF); trend fitted to the last 30 days, with a 90 % "
                  "band on the date it reaches its trip", [tag, *[c["id"] for c in channels]], unit=worst["unit"],
                  machine=_label(conn, tag), worst_channel=worst["channel"], point=worst["point"],
                  channels=channels, flags=sorted(set(flags), key=order.index),
                  data_from=min(c["data_from"] for c in channels), data_to=max(c["data_to"] for c in channels),
                  readings=sum(c["readings"] for c in channels),
                  etas=[{"what": f"{c['channel']} reaches its trip ({c['trip']:g} {c['unit']})", "point": c["point"],
                         "reaches": c["reaches_trip"], "range": c["trip_range"]}
                        for c in channels if c["reaches_trip"] and c["trip"] is not None])


def seal_gas(conn, fid: str, days: int = 120, until: str | None = None, src=None) -> dict:
    """A dry gas seal's primary vent flow (API 692) against its clean baseline and its alarm."""
    tag = _tag(conn, fid)
    pts = points_like(conn, tag, r"seal.*vent|primary vent")
    if not pts:
        raise CalcError("no dry gas seal vent flow point on that machine")
    p = pts[0]
    start, end = window_of(conn, days, until)
    keep = running(conn, tag, start, end, 60, src)
    days_, ys = daily(read({"v": p}, start, end, 60, src)["v"], keep)
    if len(days_) < 14:
        raise CalcError("fewer than 14 running days of seal vent flow")
    base = float(np.percentile(ys[: max(14, len(ys) // 4)], 50))
    now = float(np.median(ys[-3:]))
    lim = settings(conn, [p["id"]], end).get(p["id"], {})
    to_alarm, to_trip = eta(days_, ys, lim.get("hi")), eta(days_, ys, lim.get("hihi"))
    state = crossed(now, lim)
    flag = "seal_leak" if state else "seal_rising" if now >= 2 * base and to_alarm["per_day"] > 0 else None
    return result(round(now, 2), round(base, 2), flag,
                  f"primary vent flow of the dry gas seal (API 692), daily medians while running; expected is its own "
                  f"clean level (the median of the window's first quarter, {days_[0]} on); the alarm and high-high "
                  f"from the DCS / AF; trend fitted to the last 30 days", [tag, p["id"]], unit=p["uom"],
                  machine=_label(conn, tag), point=p["point"], times_clean=round(now / base, 1) if base else None,
                  alarm=lim.get("hi"), high_high=lim.get("hihi"), state=state or "normal",
                  per_week=round(to_alarm["per_day"] * 7, 3), reaches_alarm=to_alarm["reaches"],
                  reaches_high_high=to_trip["reaches"],
                  etas=[{"what": f"seal vent flow reaches its high-high ({lim['hihi']:g} {p['uom']})", "point": p["point"],
                         "reaches": to_trip["reaches"], "range": [to_trip["early"], to_trip["late"]] if to_trip["early"] else None}]
                  if to_trip["reaches"] and "hihi" in lim else [],
                  data_from=days_[0], data_to=days_[-1], readings=len(ys),
                  daily=[{"day": d, "value": round(float(y), 2)} for d, y in zip(days_, ys)][-60:])


def lube_oil(conn, fid: str, days: int = 90, until: str | None = None, src=None) -> dict:
    """The lube oil system (API 614): header pressure and supply temperature against their settings, trended."""
    tag = _tag(conn, fid)
    press = points_like(conn, tag, r"lube oil.*pressure")
    temp = points_like(conn, tag, r"lube oil.*temperature")
    if not press and not temp:
        raise CalcError("no lube oil pressure or temperature point on that machine")
    start, end = window_of(conn, days, until)
    keep = running(conn, tag, start, end, 60, src)
    pts = {"p": press[0]} if press else {}
    if temp:
        pts["t"] = temp[0]
    vals = read(pts, start, end, 60, src)
    lims = settings(conn, [p["id"] for p in pts.values()], end)
    out, flags, etas = {}, [], []
    for k, p in pts.items():
        days_, ys = daily(vals[k], keep)
        if len(days_) < 7:
            continue
        side = "lo" if k == "p" else "hi"
        lim = lims.get(p["id"], {})
        alarm, trip = (lim.get("lo"), lim.get("lolo")) if side == "lo" else (lim.get("hi"), lim.get("hihi"))
        a, t = eta(days_, ys, alarm, side), eta(days_, ys, trip, side)
        now = float(np.median(ys[-3:]))
        state = crossed(now, lim, side)
        out[k] = {"point": p["point"], "id": p["id"], "now": round(now, 2), "unit": p["uom"], "alarm": alarm,
                  "trip": trip, "state": state or "normal", "per_week": round(a["per_day"] * 7, 3),
                  "reaches_alarm": a["reaches"], "reaches_trip": t["reaches"], "data_from": days_[0],
                  "data_to": days_[-1], "readings": len(ys),
                  "daily": [{"day": d, "value": round(float(y), 2)} for d, y in zip(days_, ys)][-60:]}
        if k == "p" and state:
            flags.append("lube_pressure_low")
        elif k == "p" and _within(a["reaches"], end, SOON_DAYS):
            flags.append("lube_pressure_falling")
        elif k == "t" and state:
            flags.append("lube_oil_hot")
        if t["reaches"] and trip is not None:
            etas.append({"what": f"lube oil {'pressure' if k == 'p' else 'temperature'} reaches its trip "
                                 f"({trip:g} {p['uom']})", "point": p["point"], "reaches": t["reaches"],
                         "range": [t["early"], t["late"]] if t["early"] else None})
    if not out:
        raise CalcError("the machine hardly ran in that window")
    main = out.get("p") or out["t"]
    order = ["lube_pressure_low", "lube_pressure_falling", "lube_oil_hot"]
    return result(main["now"], main["alarm"], min(flags, key=order.index) if flags else None,
                  "lube oil header pressure against its low alarm and trip, and supply temperature against its high "
                  "alarm (API 614), daily medians while the machine runs; trends fitted to the last 30 days",
                  [tag, *[v["id"] for v in out.values()]], unit=main["unit"], machine=_label(conn, tag),
                  pressure=out.get("p"), temperature=out.get("t"), flags=flags, etas=etas,
                  data_from=main["data_from"], data_to=main["data_to"], readings=main["readings"])
