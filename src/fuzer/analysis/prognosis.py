"""Remaining life and the next window (ISO 13381-1 prognostics): will this machine make it to the next shutdown?

`remaining_life(ref)` gathers every trend on the machine that heads for a limit: vibration to ISO 20816 zone D, the
protection channels to their trips (API 670), the dry gas seal vent to its high-high, lube oil pressure to its trip,
fired hours to the hot section inspection. Each comes with the date it gets there at its current rate and the 90 %
range the trend allows, because a prognosis without its uncertainty is a guess (ISO 13381-1). The earliest is the
machine's remaining life. It is set against the next window the plant has: the next turnaround (open work orders
on a revision) or shutdown on the integrated plan. Flags `fails_before_window` when the likeliest date comes before
the window opens (act now, or pull the window forward), and `tight` when only the early end of the range does.
"""

from datetime import datetime

from .calcs import CalcError, _label, result, window_of


def _tag(conn, fid: str) -> str:
    from .rotating import _tag as tag

    return tag(conn, fid)


def next_window(conn, after: datetime) -> dict | None:
    """The next turnaround or shutdown: the earliest open work order on a revision starting after `after`, or a
    turnaround / shutdown on the integrated plan, whichever comes first."""
    found = []
    r = conn.execute(
        """
        select f->>'revision' as revision, min(left(f->>'start', 10)) as start, count(*) as orders from (
            select jsonb_object_agg(g.field, g.value #>> '{}') as f from entity e join golden g on g.fuzer_id = e.fuzer_id
            where e.kind = 'work_order' and e.retired_at is null group by e.fuzer_id) w
        where coalesce(f->>'revision', '') <> '' and f->>'start' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
          and left(f->>'start', 10) > %s and split_part(coalesce(f->>'status', ''), ' ', 1) in ('CRTD', 'REL', 'PCNF', '')
        group by 1 order by 2 limit 1
        """, (after.date().isoformat(),)).fetchone()
    if r:
        found.append({"start": r["start"], "name": f"turnaround {r['revision']}", "source": "SAP work orders",
                      "orders": r["orders"]})
    p = conn.execute("select title, start_at, kind from plan_item where kind = any(%s) and status <> 'done' "
                     "and status <> 'cancelled' and start_at > %s order by start_at limit 1",
                     (["turnaround", "shutdown"], after)).fetchone()
    if p:
        found.append({"start": p["start_at"].date().isoformat(), "name": f"{p['kind']}: {p['title']}",
                      "source": "the integrated plan"})
    return min(found, key=lambda w: w["start"]) if found else None


def _vibration_eta(conn, tag: str, until: str | None) -> list[dict]:
    from . import rotating
    from .condition import eta, projection

    v = rotating.vibration(conn, tag, until=until)
    if not v.get("daily") or v["zone"] == "D":
        return [{"what": "vibration in ISO 20816 zone D", "reaches": "now", "range": None, "point": v["point"]}] \
            if v.get("zone") == "D" else []
    import numpy as np

    days = [d["day"] for d in v["daily"]]
    e = eta(days, np.array([d["mm_s"] for d in v["daily"]]), v["zones"]["C/D"])
    if not e["reaches"]:
        return []
    return [projection(f"vibration reaches zone D ({v['zones']['C/D']:g} mm/s)", v["point"], e)]


def remaining_life(conn, fid: str, until: str | None = None) -> dict:
    """The earliest date a trend on the machine reaches its limit, with its range, against the next window."""
    from . import drivers, protection

    tag = _tag(conn, fid)
    _, end = window_of(conn, 1, until)
    etas, looked, skipped = [], [], []
    for name, fn in [("vibration", lambda: _vibration_eta(conn, tag, until)),
                     ("protection", lambda: protection.protection(conn, tag, until=until)["etas"]),
                     ("seal_gas", lambda: protection.seal_gas(conn, tag, until=until)["etas"]),
                     ("lube_oil", lambda: protection.lube_oil(conn, tag, until=until)["etas"]),
                     ("hot_section", lambda: drivers.hot_section(conn, tag, until=until)["etas"])]:
        try:
            found = fn()
        except CalcError as e:
            skipped.append({"check": name, "why": str(e)})
            continue
        looked.append(name)
        etas += [{**x, "check": name} for x in found if x.get("reaches")]
    if not looked:
        raise CalcError("no trend to project on that machine: " + "; ".join(s["why"] for s in skipped))
    window = next_window(conn, end)
    today = end.date().isoformat()

    def verdict(x):
        if x["reaches"] == "now":
            return "past it now"
        if not window:
            return "no window planned"
        if x["reaches"] < window["start"]:
            return "before the window"
        if x.get("range") and x["range"][0] and x["range"][0] < window["start"]:
            return "may not make it"
        return "makes the window"
    for x in etas:
        x["verdict"] = verdict(x)
        x["days"] = 0 if x["reaches"] == "now" else (datetime.fromisoformat(x["reaches"]) - end).days
    etas.sort(key=lambda x: "0000" if x["reaches"] == "now" else x["reaches"])
    first = etas[0] if etas else None
    flag = None
    if first and first["verdict"] in ("past it now", "before the window"):
        flag = "fails_before_window"
    elif first and first["verdict"] == "may not make it":
        flag = "tight"
    return result(first["days"] if first else None, None, flag,
                  "every trend on the machine that heads for a limit (vibration to ISO 20816 zone D, API 670 channels "
                  "to their trips, dry gas seal vent to its high-high, lube oil pressure to its trip, fired hours to "
                  "the hot section inspection), each at its last 30 days' rate with a 90 % range on the slope (ISO "
                  "13381-1); the earliest against the next turnaround or shutdown (open SAP orders on a revision, the "
                  f"integrated plan), as of {today}", [tag], unit="days of remaining life",
                  machine=_label(conn, tag), first=first, etas=etas, window=window, checked=looked, not_checked=skipped,
                  at=today)
