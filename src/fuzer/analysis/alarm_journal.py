"""The alarms that sounded (#859): from a loaded DCS alarm and event journal where it covers the window, else
derived from the limits (each point going past an enabled alarm limit while its unit runs, `limits.excursions`).

A journal row is one event on one alarm (`dcs_alarm_journal.yaml`): ALARM, ACK, RTN, SHELVE, UNSHELVE, SUPPRESS,
UNSUPPRESS. An alarm runs from an ALARM to the next RTN on the same tag and alarm; an ALARM while one is already on
(it rang again when its shelf ran out) starts a new one. Each alarm is matched to its point through the DCS alarm
limits (`point_limit` rows from `dcs:alarm:<tag>>…`), so the result has the shape `limits.excursions` gives and the
analyses read either the same way. A journal row whose tag has no DCS alarm limit is left out and counted.

What the journal knows that limits can't: alarms to the second (chattering within a minute is seen as it happened),
alarms whose unit was stopped, and alarms set aside. Shelved and suppressed alarms are counted on their own.
"""

from datetime import datetime, timedelta

SOURCE, RECORD_TYPE = "dcs", "alarm_event"
LOOKBACK = timedelta(days=14)  # read this far before the window, so alarms already on when it opens are known
EDGE = timedelta(hours=24)  # a journal covers a window that ends up to this long after its last event


def _dt(s) -> datetime:
    return s if isinstance(s, datetime) else datetime.fromisoformat(str(s)).replace(tzinfo=None)


def coverage(conn) -> tuple[datetime, datetime] | None:
    """The first and last event time of the loaded journal, or None when no journal is loaded."""
    r = conn.execute("select min(payload->>'timestamp') as a, max(payload->>'timestamp') as b from source_record "
                     "where source = %s and record_type = %s", (SOURCE, RECORD_TYPE)).fetchone()
    return (_dt(r["a"]), _dt(r["b"])) if r and r["a"] else None


def covers(conn, start: datetime, end: datetime) -> bool:
    c = coverage(conn)
    return bool(c) and c[0] <= start and c[1] + EDGE >= end


def _alarm_points(conn) -> dict[tuple[str, str], dict]:
    """(DCS tag, alarm) -> its point and limit, from the DCS alarm limits."""
    out = {}
    for r in conn.execute("select record, point_id, limit_type, value from point_limit where source = %s and basis = 'alarm'",
                          (SOURCE,)):
        key = r["record"].split(":", 2)[-1].split("#")[0]
        tag, _, alarm = key.partition(">")
        out[(tag, alarm)] = {"point_id": r["point_id"], "limit_type": r["limit_type"], "limit_value": r["value"],
                             "record": r["record"]}
    return out


def alarms(conn, point_ids: list[str] | None, start: datetime, end: datetime) -> dict:
    """The journal's alarms overlapping [start, end] on these points (None: all), as excursion-shaped dicts, with
    the counts of shelvings, suppressions and rows that matched no point."""
    from ..sources.timeseries import _point
    from .limits import units

    limits = _alarm_points(conn)
    unit_of = units(conn, sorted({v["point_id"] for v in limits.values()}))
    keep = set(point_ids) if point_ids is not None else None
    rows = conn.execute(
        "select payload from source_record where source = %s and record_type = %s "
        "and payload->>'timestamp' >= %s and payload->>'timestamp' <= %s order by payload->>'timestamp'",
        (SOURCE, RECORD_TYPE, (start - LOOKBACK).isoformat(timespec="seconds"), end.isoformat(timespec="seconds")))
    open_: dict[tuple[str, str], dict] = {}
    found, unmatched, shelved, suppressed, acked = [], set(), 0, 0, 0
    names: dict[str, str] = {}

    def close(k, at):
        x = open_.pop(k, None)
        if x is not None:
            x["end"] = at
            found.append(x)

    for r in rows:
        e = r["payload"]
        k = (e.get("tag"), e.get("alarm"))
        lim = limits.get(k)
        if lim is None:
            unmatched.add(k[0])
            continue
        if keep is not None and lim["point_id"] not in keep:
            continue
        at, kind = _dt(e["timestamp"]), e.get("event")
        if kind == "ALARM":
            close(k, at)
            pid = lim["point_id"]
            if pid not in names:
                names[pid] = (_point(conn, pid) or {}).get("point") or pid
            open_[k] = {"point_id": pid, "point": names[pid], "unit_id": unit_of.get(pid), "start": at, "end": None,
                        "basis": "alarm", "level": e.get("priority"), "record": lim["record"],
                        "limit_type": lim["limit_type"], "limit_value": float(e.get("setpoint") or lim["limit_value"]),
                        "peak": float(e["value"]) if e.get("value") not in (None, "") else None, "source": "journal"}
        elif kind == "RTN":
            close(k, at)
        elif at >= start:
            shelved += kind == "SHELVE"
            suppressed += kind == "SUPPRESS"
            acked += kind == "ACK"
    found += open_.values()
    found = [x for x in found if (x["end"] is None or x["end"] >= start) and x["start"] <= end]
    return {"alarms": sorted(found, key=lambda x: x["start"]), "shelved": shelved, "suppressed": suppressed,
            "acknowledged": acked, "unmatched_tags": sorted(t for t in unmatched if t)[:20]}


def sounded(conn, pack, point_ids: list[str], start: datetime, end: datetime, src=None) -> tuple[list[dict], dict]:
    """The alarms on these points over [start, end], and where they came from: `{"source": "journal", ...counts}`
    when a loaded journal covers the window, else `{"source": "limits"}` with each point past an enabled alarm limit
    while its unit runs (10 minute steps)."""
    if covers(conn, start, end):
        j = alarms(conn, point_ids, start, end)
        return j.pop("alarms"), {"source": "journal", **j}
    from . import limits

    ex = [x for x in limits.excursions(conn, pack, point_ids, start, end, 10, src) if x["basis"] == "alarm"]
    return ex, {"source": "limits"}
