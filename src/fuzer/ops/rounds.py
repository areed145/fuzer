"""Operator rounds (P5.4, #716): a work source of checklists per area and equipment, done on the phone each shift.

    points(conn)          the checklist points from the `rounds` source pack (round_point entities): what to read or
                          check, on which equipment, with its unit and limits, in round order
    open_shift(conn)      one run of every round for the current shift (`handover.shift_times`), for the operator
                          role; a run whose shift is over is closed as done, partly done or missed
    runs(conn)            this shift's runs with how far each has got
    run(conn, id)         a run's points, each with what was taken
    take(conn, ...)       an operator's reading or check on a point (P5.4b): kept on the run and, for a reading, as a
                          measurement on its asset; outside its limits, or a failed check, raises one round_finding
                          through the finding contract, which routes a case to the IOC
    per_shift(conn)       rounds done, partly done and missed per shift, with the checks not taken (P5.4c), for Work
                          performance; of_shift(conn, start, end) the same in words for the shift handover

Read from the records, never from equipment type: a site's rounds are its own export.
"""

import re
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from ..core import findings
from ..core.exceptions import ExceptionError, exception_type, raise_exception

ROLE = "operator"
METHOD = "Operator round"
ROUND_FINDING = exception_type(
    "round_finding", "Round finding", role="role:ops_lead", audience=("ioc", "field"),
    description="A reading outside its limits, or a failed check, taken by an operator on a round.").name


def points(conn) -> list[dict]:
    """Every checklist point, grouped in round order, each with the equipment it is on."""
    rows = conn.execute(
        """
        select p.fuzer_id as id, pl.label as point_id, c.dst as asset, al.label as asset_label,
               max(case when g.field = 'round' then g.value #>> '{}' end) as round,
               max(case when g.field = 'area' then g.value #>> '{}' end) as area,
               max(case when g.field = 'what' then g.value #>> '{}' end) as what,
               max(case when g.field = 'kind' then g.value #>> '{}' end) as kind,
               max(case when g.field = 'unit' then g.value #>> '{}' end) as unit,
               max(case when g.field = 'low' then (g.value #>> '{}')::numeric end) as low,
               max(case when g.field = 'high' then (g.value #>> '{}')::numeric end) as high,
               max(case when g.field = 'sort' then (g.value #>> '{}')::numeric end) as sort
        from entity p
        left join entity_label pl on pl.fuzer_id = p.fuzer_id
        left join edge c on c.src = p.fuzer_id and c.rel = 'checks'
        left join entity_label al on al.fuzer_id = c.dst
        left join golden g on g.fuzer_id = p.fuzer_id
        where p.kind = 'round_point'
        group by p.fuzer_id, pl.label, c.dst, al.label
        """).fetchall()
    out = [{**r, "kind": (r["kind"] or "reading").lower(), "unit": r["unit"] or None,
            "low": float(r["low"]) if r["low"] is not None else None,
            "high": float(r["high"]) if r["high"] is not None else None} for r in rows if r["round"]]
    return sorted(out, key=lambda p: (p["round"], p["sort"] if p["sort"] is not None else 1e9, p["point_id"] or ""))


def rounds(conn) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in points(conn):
        out.setdefault(p["round"], []).append(p)
    return out


def shift(conn, now: datetime | None = None) -> tuple[datetime, datetime]:
    """The shift under way at `now`: (the latest shift change at or before it, the next one)."""
    from .handover import _tz, shift_times

    now = now or datetime.now(UTC)
    tz = _tz()
    local = now.astimezone(tz)
    changes = sorted(datetime.combine(local.date() + timedelta(days=d), t, tz)
                     for d in (-1, 0, 1) for t in shift_times(conn))
    start = max(c for c in changes if c <= local)
    end = min(c for c in changes if c > local)
    return start.astimezone(UTC), end.astimezone(UTC)


def _close_past(conn, now: datetime) -> int:
    """Runs whose shift is over: done when every point was taken, partly done when some were, else missed."""
    return conn.execute(
        """
        update round_run r set closed_at = %(now)s,
               state = case when x.taken >= r.points then 'done' when x.taken > 0 then 'partly_done' else 'missed' end
        from (select r2.id, (select count(*) from round_reading t where t.run_id = r2.id) as taken
              from round_run r2 where r2.state = 'open' and r2.shift_end <= %(now)s) x
        where r.id = x.id
        """, {"now": now}).rowcount


def open_shift(conn, now: datetime | None = None) -> dict:
    """Open one run of every round for the shift under way (once), and close the runs of shifts that are over."""
    now = now or datetime.now(UTC)
    closed = _close_past(conn, now)
    start, end = shift(conn, now)
    opened = 0
    for name, pts in rounds(conn).items():
        opened += conn.execute(
            "insert into round_run (round, shift_start, shift_end, role, points) values (%s, %s, %s, %s, %s) "
            "on conflict (round, shift_start) do nothing", (name, start, end, ROLE, len(pts))).rowcount
    return {"opened": opened, "closed": closed, "shift_start": start, "shift_end": end}


def runs(conn, now: datetime | None = None) -> list[dict]:
    """This shift's runs, each with its area, points, how many are taken and how many found something."""
    now = now or datetime.now(UTC)
    open_shift(conn, now)
    start, _ = shift(conn, now)
    by_round = rounds(conn)
    rows = conn.execute(
        """
        select r.*, (select count(*) from round_reading t where t.run_id = r.id)::int as taken,
               (select count(*) from round_reading t where t.run_id = r.id and t.passed is false)::int as found
        from round_run r where r.shift_start = %s order by r.round
        """, (start,)).fetchall()
    return [{**r, "area": (by_round.get(r["round"]) or [{}])[0].get("area")} for r in rows]


def run(conn, run_id: int) -> dict:
    """A run with each of its points in round order and what was taken on it."""
    r = conn.execute("select * from round_run where id = %s", (run_id,)).fetchone()
    if r is None:
        raise ExceptionError(f"no round run {run_id}")
    taken = {t["point"]: t for t in conn.execute(
        "select * from round_reading where run_id = %s order by id", (run_id,)).fetchall()}
    pts = [{**p, "taken": taken.get(p["id"])} for p in rounds(conn).get(r["round"], [])]
    return {**r, "points": pts, "taken": len(taken), "area": pts[0]["area"] if pts else None}


def _quantity(what: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (what or "reading").lower()).strip("_") or "reading"


def _naive(at: datetime) -> datetime:
    """A moment as the measurement table keeps a source's own times: the site's local clock, no zone."""
    from .handover import _tz

    return at.astimezone(_tz()).replace(tzinfo=None)


def _record(run_id: int, point: dict) -> str:
    return f"rounds:reading:{run_id}:{point['point_id'] or point['id']}"


def _limits(p: dict) -> str:
    lo, hi, unit = p["low"], p["high"], f" {p['unit']}" if p["unit"] else ""
    if lo is not None and hi is not None:
        return f"{lo:g} to {hi:g}{unit}"
    return f"at least {lo:g}{unit}" if lo is not None else f"at most {hi:g}{unit}" if hi is not None else ""


def _finding(r: dict, p: dict, value: float | None, passed: bool, note: str | None, by: str, at: datetime):
    reading = p["kind"] == "reading"
    side = None
    if reading and p["high"] is not None and value > p["high"]:
        side = "high"
    elif reading and p["low"] is not None and value < p["low"]:
        side = "low"
    return findings.Finding(
        "rounds.reading", findings.DETERMINISTIC if reading else findings.EXTERNAL,
        method="A round reading outside its limits" if reading else "A failed check on a round",
        start=at.isoformat(timespec="minutes"), data={"round": r["round"], "point": p["point_id"], "run": r["id"]},
        items=(findings.fact("Round", r["round"]), findings.fact("Check", p["what"]),
               findings.fact("Reading", value, p["unit"]) if reading else findings.fact("Result", "Failed"),
               findings.fact("Limits", _limits(p)) if reading else None,
               findings.fact("Outside", side) if side else None,
               findings.fact("Note", note), findings.fact("Taken by", by), findings.at("Taken", at),
               findings.asset("On", p["asset"])))


def _taken_at(conn, at) -> tuple[datetime, bool]:
    """When a reading the phone kept was taken (P5.17d): its own time, never in the future or more than a week back.
    Also whether it reached the server well after it was taken."""
    now = conn.execute("select now() as t").fetchone()["t"]
    if at is None:
        return now, False
    if not isinstance(at, datetime):
        try:
            at = datetime.fromisoformat(str(at))
        except ValueError:
            raise ExceptionError("the reading's time is not a date and time") from None
    if at.tzinfo is None:
        at = at.replace(tzinfo=now.tzinfo)
    at = min(max(at, now - timedelta(days=7)), now)
    return at, now - at > timedelta(minutes=2)


def take(conn, run_id: int, point: str, by: str, value: float | None = None, passed: bool | None = None,
         note: str | None = None, at: datetime | str | None = None, client_id: str | None = None) -> dict:
    """An operator's reading (a number) or check (passed or not) on one point of an open round. Taking it again
    corrects it. A reading is kept as a measurement on its asset; one outside its limits, or a failed check, raises
    a round_finding on the point (one open per point, so a second bad reading adds to it) with the reading as
    evidence. Returns the point as taken, with the exception it raised.

    A reading the phone kept without signal (P5.17d) comes with its `client_id` and the time it was taken: it is
    applied once (a resend changes nothing), keeps that time, and an earlier-taken one never overwrites a later one,
    whatever order they arrive in. One taken during the shift is accepted after the shift's run has closed, and the
    run's state is counted again."""
    by = (by or "").strip()
    if not by:
        raise ExceptionError("say who took it")
    note = (note or "").strip() or None
    client_id = (str(client_id).strip()[:80] or None) if client_id else None
    if client_id:
        at, late = _taken_at(conn, at)
    else:
        at, late = at if isinstance(at, datetime) else datetime.now(UTC), False
    r = conn.execute("select * from round_run where id = %s for update", (run_id,)).fetchone()
    if r is None:
        raise ExceptionError(f"no round run {run_id}")
    taken_in_shift = client_id is not None and r["shift_start"] <= at <= r["shift_end"]
    if r["state"] != "open" and not taken_in_shift:
        raise ExceptionError(f"{r['round']} for that shift is closed ({r['state'].replace('_', ' ')})")
    p = next((x for x in rounds(conn).get(r["round"], []) if point in (x["id"], x["point_id"])), None)
    if p is None:
        raise ExceptionError(f"{point} is not on {r['round']}")
    if client_id:
        before = conn.execute("select * from round_reading where run_id = %s and point = %s",
                              (run_id, p["id"])).fetchone()
        if before and (before["client_id"] == client_id or before["at"] > at):
            # Sent again after the answer was lost, or a later reading of this point is already here: it stands.
            return {**p, "taken": before, "exception": None, "kept": "repeat" if before["client_id"] == client_id
                    else "later_reading_stands"}
    if p["kind"] == "reading":
        if value is None:
            raise ExceptionError(f"enter the reading for {p['what']}")
        value = float(value)
        passed = (p["low"] is None or value >= p["low"]) and (p["high"] is None or value <= p["high"])
    else:
        if passed is None:
            raise ExceptionError(f"say whether {p['what']} passed")
        value = None
        if not passed and not note:
            raise ExceptionError("say what you found")
    raised = None
    if not passed:
        raised = raise_exception(
            conn, ROUND_FINDING, p["point_id"] or p["id"], f"{p['what']} on {p['asset_label'] or r['round']}"
            + (f": {value:g}{' ' + p['unit'] if p['unit'] else ''}, limits {_limits(p)}" if value is not None else
               f" failed: {note}"), by, refs=[p["asset"]] if p["asset"] else [], source="rounds",
            finding=_finding(r, p, value, passed, note, by, at))
    taken = conn.execute(
        """
        insert into round_reading (run_id, point, value, passed, note, by, at, exception_id, client_id, received_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, case when %s then now() end)
        on conflict (run_id, point) do update set value = excluded.value, passed = excluded.passed,
            note = excluded.note, by = excluded.by, at = excluded.at,
            exception_id = coalesce(excluded.exception_id, round_reading.exception_id),
            client_id = excluded.client_id, received_at = excluded.received_at
        returning *
        """, (run_id, p["id"], value, passed, note, by, at, raised and raised["id"], client_id, late)).fetchone()
    if r["state"] != "open":  # taken in the shift, arrived after it closed: count the run again
        conn.execute(
            """
            update round_run set state = case when x.taken >= points then 'done' when x.taken > 0 then 'partly_done'
                else 'missed' end
            from (select count(*) as taken from round_reading where run_id = %(id)s) x where id = %(id)s
            """, {"id": run_id})
    if value is not None and p["asset"]:
        conn.execute(
            """
            insert into measurement (record, quantity, fuzer_id, at, value, unit, method, location)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (record, quantity) do update set value = excluded.value, at = excluded.at
            """, (_record(run_id, p), _quantity(p["what"]), p["asset"], _naive(at),
                  value, p["unit"], METHOD, r["round"]))
    return {**p, "taken": taken, "exception": raised and {"id": raised["id"], "raised": raised["raised"]}}


def measurements(conn) -> dict:
    """Every reading taken on a round, as rows for the measurement table, so a rebuild keeps them on their asset."""
    by_id = {p["id"]: p for p in points(conn)}
    out = {}
    for t in conn.execute("select * from round_reading where value is not null order by id"):
        p = by_id.get(t["point"])
        if p and p["asset"]:
            q = _quantity(p["what"])
            out[(_record(t["run_id"], p), q)] = (_record(t["run_id"], p), q, p["asset"],
                                                 _naive(t["at"]), t["value"], p["unit"],
                                                 None, None, METHOD, p["round"])
    return out


def _state(points: int, taken: int, closed: bool) -> str:
    if taken >= points:
        return "done"
    if not closed:
        return "open"
    return "partly_done" if taken else "missed"


def per_shift(conn, limit: int = 14, now: datetime | None = None) -> list[dict]:
    """The last `limit` shifts with rounds, newest first: how many rounds were done, partly done, missed or still
    open, the checks not taken on the ones closed, and the checks that found something."""
    now = now or datetime.now(UTC)
    _close_past(conn, now)
    rows = conn.execute(
        """
        select r.shift_start, r.shift_end, count(*)::int as rounds,
               count(*) filter (where r.state = 'done')::int as done,
               count(*) filter (where r.state = 'partly_done')::int as partly_done,
               count(*) filter (where r.state = 'missed')::int as missed,
               count(*) filter (where r.state = 'open')::int as open,
               coalesce(sum(greatest(r.points - x.taken, 0)) filter (where r.state <> 'open'), 0)::int as not_taken,
               coalesce(sum(x.found), 0)::int as found
        from round_run r, lateral (select count(*) as taken, count(*) filter (where t.passed is false) as found
                                   from round_reading t where t.run_id = r.id) x
        group by r.shift_start, r.shift_end order by r.shift_start desc limit %s
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def of_shift(conn, start: datetime, end: datetime) -> list[dict]:
    """Each round of the shift that started in [start, end), as the handover says it: done, partly done (with the
    checks not taken) or missed, and what it found."""
    out = []
    for r in conn.execute(
        """
        select r.id, r.round, r.points, r.closed_at, r.shift_end,
               (select count(*) from round_reading t where t.run_id = r.id)::int as taken,
               (select count(*) from round_reading t where t.run_id = r.id and t.passed is false)::int as found
        from round_run r where r.shift_start >= %s and r.shift_start < %s order by r.round
        """, (start, end)).fetchall():
        state = _state(r["points"], r["taken"], True)
        said = {"done": "done", "partly_done": f"partly done, {r['points'] - r['taken']} of {r['points']} checks not "
                                              "taken", "missed": "missed"}[state]
        out.append({"id": r["id"], "state": state, "text": f"{r['round']}: {said}"
                    + (f"; {r['found']} found something" if r["found"] else "")})
    return out


# ---- readings taken before the move to US oilfield units -------------------------------------------------------

# A round point's metric unit before the exports moved to US oilfield units, and the US unit it reads in now.
METRIC = {"psig": "barg", "degF": "degC"}
_METRIC_TITLE = re.compile(r": (-?\d+(?:\.\d+)?) (barg|degC), limits [^:]*$")


def metric_readings_moved(conn) -> dict:
    """Readings taken on a round before its points moved to US oilfield units were kept as the numbers taken, in
    bar or degrees Celsius, and are read today in psig or degrees Fahrenheit: an 89.42 °C bearing plotted at 89 °F
    under a °F limit, beside a finding titled "89.42 degC, limits 0 to 85 degC". This puts them in US units: each
    round finding still titled in a metric unit (its title, its case's title and its Reading and Limits), and each
    reading taken before the move that reads as the metric number it was, kept on the run and as a measurement on its
    asset. Idempotent: what it converted reads in US units after."""
    from ..core import units

    # When the move happened: the first finding titled in a US unit after the last one still titled in a metric
    # unit. Read before converting, so what this converts doesn't move it.
    move = conn.execute(
        """
        select min(last_seen) as t from exception_record
        where type = %(t)s and title ~ ' (psig|degF), limits '
          and last_seen > coalesce((select max(last_seen) from exception_record
                                    where type = %(t)s and title ~ ' (barg|degC), limits '), '-infinity')
        """, {"t": ROUND_FINDING}).fetchone()["t"]
    by_point = {p["point_id"]: p for p in points(conn) if p["kind"] == "reading" and p["unit"] in METRIC}
    as_taken: set[tuple[str, float]] = set()  # (point, metric value) a metric title names
    findings_moved = 0
    for e in conn.execute("select id, key, title, evidence, item_id from exception_record "
                          "where type = %s and title ~ ' (barg|degC), limits '", (ROUND_FINDING,)).fetchall():
        m = _METRIC_TITLE.search(e["title"])
        if not m:
            continue
        metric, was = float(m.group(1)), m.group(2)
        us = next(k for k, v in METRIC.items() if v == was)
        value = round(units.convert(metric, was, us), 2)
        p = by_point.get(e["key"])
        if p and p["unit"] == us:
            limits = _limits(p)
            as_taken.add((p["id"], metric))
        else:
            # the point reads something else now (the checklist changed): its own limits, converted as written
            limits = re.sub(r"-?\d+(?:\.\d+)?", lambda n, was=was, us=us: f"{round(units.convert(float(n.group()), was, us), 1):g}",
                            e["title"][m.end(2):].split("limits ", 1)[1]).replace(was, us)
        title = f"{e['title'][:m.start()]}: {value:g} {us}, limits {limits}"
        evidence = dict(e["evidence"] or {})
        f = evidence.get(findings.KEY)
        if isinstance(f, dict):
            f["items"] = [{**i, "value": value, "unit": us} if i.get("label") == "Reading"
                          else {**i, "value": limits} if i.get("label") == "Limits" else i
                          for i in f.get("items") or []]
        conn.execute("update exception_record set title = %s, evidence = %s where id = %s",
                     (title, Jsonb(evidence), e["id"]))
        if e["item_id"]:
            conn.execute("update work_item set title = %s where id = %s and title = %s", (title, e["item_id"], e["title"]))
        findings_moved += 1
    # Readings: those a metric title names, and the rest taken before the move that sit nearer the middle of the
    # point's limits once converted than as they are (a reading is taken near the middle; 42 under 32 to 185 °F
    # was 42 °C).
    by_id = {p["id"]: p for p in by_point.values()}
    readings_moved = 0
    for t in conn.execute("select id, run_id, point, value, at from round_reading where value is not null "
                          "and point = any(%s)", (list(by_id),)).fetchall():
        p = by_id[t["point"]]
        converted = units.convert(t["value"], METRIC[p["unit"]], p["unit"])
        if converted is None:
            continue
        if (p["id"], t["value"]) not in as_taken:
            if (move is not None and t["at"] >= move) or (p["low"] is None and p["high"] is None):
                continue
            mid = ((p["low"] if p["low"] is not None else p["high"]) + (p["high"] if p["high"] is not None else p["low"])) / 2
            if abs(converted - mid) >= abs(t["value"] - mid):
                continue
        converted = round(converted, 2)
        conn.execute("update round_reading set value = %s where id = %s", (converted, t["id"]))
        conn.execute("update measurement set value = %s, unit = %s where record = %s and quantity = %s",
                     (converted, p["unit"], _record(t["run_id"], p), _quantity(p["what"])))
        readings_moved += 1
    return {"findings": findings_moved, "readings": readings_moved}
