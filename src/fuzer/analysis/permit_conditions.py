"""Permit conditions (#370): the regulatory permits and their conditions from the site's register, what each condition
asks for and when, and the values on record set beside the limit as the register writes it.

    conditions(conn, as_of, months, ref)   every condition in force (or one, or those of one permit): its owner and
                                           next due date, and per period in the window the recorded figure (the
                                           samples' maximum, mean or total, as the condition states) against its
                                           limit: within, exceeded, or no limit on record; a condition not mapped to
                                           a recorded value, or with none in the window, is unknown

A limit comes only from the register, never from a design value, a typical figure or another permit. A value comes
only from records: measurements on what the condition applies to (laboratory results, survey readings), or the daily
reports that report on it (a facility's flared volume). A register limit in another unit is converted only where the
units table knows the conversion; otherwise the two aren't compared. A condition with nothing recorded is unknown,
never compliant. Whether the site complies is for the accountable people to decide; this sets the record beside the
words of the permit.
"""

from datetime import date, timedelta

from ..core import units
from .calcs import CalcError

BASES = ("each_sample", "period_max", "period_mean", "period_sum")
PERIOD_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}
VERDICT_ORDER = ("exceeded", "unknown", "no_limit", "within")


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _register(conn) -> list[dict]:
    rows = conn.execute(
        """
        select e.fuzer_id as id, l.label, jsonb_object_agg(g.field, g.value #>> '{}') as f,
               (select jsonb_agg(jsonb_build_object('id', a.dst, 'label', coalesce(al.label, a.dst)) order by a.dst)
                from edge a left join entity_label al on al.fuzer_id = a.dst
                where a.src = e.fuzer_id and a.rel = 'applies_to' and a.valid_to is null) as applies_to
        from entity e left join entity_label l using (fuzer_id) join golden g using (fuzer_id)
        where e.kind = 'permit_condition' and e.retired_at is null and e.merged_into is null
        group by e.fuzer_id, l.label
        """).fetchall()
    return [{"id": r["id"], "condition": r["label"], **r["f"], "applies_to": r["applies_to"] or []} for r in rows]


def _values(conn, ids: list[str], quantity: str, since: date, until: date) -> list[dict]:
    """The recorded values of `quantity` on what a condition applies to: measurements first, then the numeric field
    of that name on the daily reports that report on it."""
    got = [dict(r) for r in conn.execute(
        "select m.at::date as day, m.value, m.unit, m.qualifier, m.location, m.record, coalesce(l.label, m.fuzer_id) as on_"
        " from measurement m left join entity_label l using (fuzer_id)"
        " where m.fuzer_id = any(%s) and m.quantity = %s and m.at >= %s and m.at < %s order by m.at",
        (ids, quantity, since, until)).fetchall()]
    if got:
        return got
    return [dict(r) for r in conn.execute(
        """
        select (t.value #>> '{}')::date as day, (v.value #>> '{}')::float as value, null as unit, null as qualifier,
               null as location, 'report:' || r.src as record, coalesce(l.label, r.dst) as on_
        from edge r join golden v on v.fuzer_id = r.src and v.field = %s
        join golden t on t.fuzer_id = r.src and t.field = 'report_date'
        left join entity_label l on l.fuzer_id = r.dst
        where r.rel = 'reports_on' and r.valid_to is null and r.dst = any(%s)
          and jsonb_typeof(v.value) = 'number' and (t.value #>> '{}') >= %s and (t.value #>> '{}') < %s
        order by 1
        """, (quantity, ids, since.isoformat(), until.isoformat())).fetchall()]


def _period(d: date, months: int) -> date:
    return date(d.year, (d.month - 1) // months * months + 1, 1)


def _next(start: date, months: int) -> date:
    m = start.month - 1 + months
    return date(start.year + m // 12, m % 12 + 1, 1)


def _figure(vals: list[dict], basis: str) -> tuple[float, dict | None]:
    """The period's figure, and the sample that set it where one did."""
    if basis == "period_mean":
        return sum(v["value"] for v in vals) / len(vals), None
    if basis == "period_sum":
        return sum(v["value"] for v in vals), None
    top = max(vals, key=lambda v: v["value"])
    return top["value"], top


def _one(conn, c: dict, today: date, months_back: int) -> dict:
    out = {k: c.get(k) for k in ("id", "condition", "permit", "description", "text", "owner", "regulator",
                                 "frequency", "quantity", "basis", "unit")}
    out["applies_to"] = [a["label"] for a in c["applies_to"]]
    limit, kind = _num(c.get("limit_value")), (c.get("limit_type") or "max")
    out["limit"] = {"value": limit, "type": kind, "unit": c.get("unit"), "as_written": c.get("text")} if limit is not None else None
    freq = c.get("frequency") or ""
    months = PERIOD_MONTHS.get(freq)
    due_days = int(_num(c.get("report_due_days")) or 0)
    if months:
        p = _period(today, months)  # a period's return falls due `due_days` after its last day
        out["next_due"] = (p - timedelta(days=1) + timedelta(days=due_days)).isoformat()
        if out["next_due"] < today.isoformat():
            out["next_due"] = (_next(p, months) - timedelta(days=1) + timedelta(days=due_days)).isoformat()
    elif c.get("due_date"):
        out["next_due"] = c["due_date"][:10]
        if out["next_due"] < today.isoformat():
            out["past_due"] = True
    if not c.get("quantity") or not c["applies_to"]:
        return out | {"verdict": "unknown", "periods": [],
                      "why": "not mapped to a recorded value: the register names no measured quantity or nothing it "
                             "applies to, so the record can't be set beside it"
                             + ("; the due date has passed and the record doesn't say it was met"
                                if out.get("past_due") else "")}
    basis = c.get("basis") if c.get("basis") in BASES else "each_sample"
    out["basis"] = basis
    months = months or 1
    start = _period(today, months)
    for _ in range(max(1, months_back // months)):
        start = date(*_back(start, months))
    end = today + timedelta(days=1)  # the records up to and including the day
    vals = _values(conn, [a["id"] for a in c["applies_to"]], c["quantity"], start, end)
    if not vals:
        return out | {"verdict": "unknown", "periods": [],
                      "why": f"no recorded {c['quantity']} on {', '.join(out['applies_to'])} since {start.isoformat()}"}
    stored = {v["unit"] for v in vals if v["unit"]}
    lim_stored, lim_unit = units.normalise(limit, c.get("unit")) if limit is not None else (None, None)
    comparable = limit is not None and (not stored or stored == {lim_unit})
    if limit is not None and not comparable:
        out["why"] = (f"the register's limit is in {c.get('unit')} and the records in {', '.join(sorted(stored))}, with "
                      "no conversion known: not compared")
    periods, p = [], start
    while p < end:
        q = _next(p, months)
        mine = [v for v in vals if p <= v["day"] < q]
        if mine:
            fig, at = _figure(mine, basis)
            row = {"period": p.isoformat()[:7] if months == 1 else f"{p.isoformat()[:7]} to {(_next(p, months) - timedelta(days=1)).isoformat()[:7]}",
                   "from": p.isoformat(), "to": (min(q, end) - timedelta(days=1)).isoformat(), "samples": len(mine),
                   "figure": round(fig, 4), "unit": next(iter(stored), c.get("unit")),
                   "below_detection": sum(1 for v in mine if v["qualifier"] == "<"),
                   "complete": q <= end}
            if at:
                row["set_by"] = {"day": at["day"].isoformat(), "record": at["record"], "location": at["location"]}
            if comparable:
                over = fig > lim_stored if kind == "max" else fig < lim_stored
                row["status"] = "exceeded" if over else "within"
                row["margin"] = round((lim_stored - fig) if kind == "max" else (fig - lim_stored), 4)
            else:
                row["status"] = "no_limit" if limit is None else "not_compared"
            periods.append(row)
        p = q
    out["periods"] = periods
    exceeded = [r["period"] for r in periods if r["status"] == "exceeded"]
    out["exceeded"] = exceeded
    if limit is None:
        out["verdict"] = "no_limit"
        out["why"] = ("the register states no numeric limit for this condition, so the values are shown and none is "
                      "inferred")
    elif not comparable:
        out["verdict"] = "unknown"
    else:
        out["verdict"] = "exceeded" if exceeded else "within"
        out["why"] = (f"{len(exceeded)} of {len(periods)} periods over the limit as written: " + ", ".join(exceeded)
                      if exceeded else f"every period on record within the limit as written ({len(periods)} periods)")
    return out


def _back(d: date, months: int) -> tuple[int, int, int]:
    m = d.month - 1 - months
    return d.year + m // 12, m % 12 + 1, 1


def conditions(conn, as_of: str | None = None, months: int = 12, ref: str | None = None) -> dict:
    """Each permit condition in force (or those matching `ref`: a condition, a permit, or what one applies to), with
    its next due date and the recorded figure per period against the limit as written; exceeded and unknown first."""
    from ..graph import operations as ops

    today = date.fromisoformat((as_of or ops.as_of(conn))[:10])
    reg = _register(conn)
    if not reg:
        return {"as_of": today.isoformat(), "conditions": [], "verdict": "no_register", "summary": {"conditions": 0},
                "note": "No permit or obligation register on record, so no condition can be tracked. Compliance stays "
                        "with the accountable people."}
    reg = [c for c in reg if (c.get("status") or "in force") == "in force"]
    if ref:
        want = ref.upper()
        reg = [c for c in reg if want in {(c["condition"] or "").upper(), (c.get("permit") or "").upper(),
                                          c["id"].upper(), *(a["label"].upper() for a in c["applies_to"])}]
        if not reg:
            raise CalcError("no permit condition in force by that id, permit, or on that tag or facility")
    rows = [_one(conn, c, today, months) for c in reg]
    rows.sort(key=lambda r: (VERDICT_ORDER.index(r["verdict"]), r.get("permit") or "", r["condition"] or ""))
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICT_ORDER}
    soon = sorted((r for r in rows if r.get("next_due") and r["next_due"] <= (today + timedelta(days=30)).isoformat()),
                  key=lambda r: r["next_due"])
    return {"as_of": today.isoformat(), "months": months, "conditions": rows,
            "verdict": "exceeded" if counts["exceeded"] else "unknown" if counts["unknown"] else "within",
            "summary": {"conditions": len(rows), **counts,
                        "exceeded": [f"{r['condition']} ({', '.join(r['applies_to'])})" for r in rows
                                     if r["verdict"] == "exceeded"],
                        "due_in_30_days": [{"condition": r["condition"], "due": r["next_due"], "owner": r.get("owner")}
                                           for r in soon]},
            "source": "Permit and obligation register (conditions, limits as written, owners), laboratory results, "
                      "survey readings and daily reports",
            "note": ("Limits are the register's own words and figures; none is inferred. A condition without a mapped "
                     "value or without a value in the window is unknown, not compliant. Whether the site complies, and "
                     "what to report, stays with the accountable owner.")}
