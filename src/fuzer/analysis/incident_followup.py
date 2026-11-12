"""Repeat incidents and corrective work ageing (#350): does a problem come back on the same unit, and does the work raised
for it close? Read for an HSE review, never a compliance judgement.

    repeats(conn, since, until)   per unit, its incidents in the window, the days between them, and any that came back
                                  after linked corrective work was closed; then every linked order, open or closed,
                                  with its age

Linked work is a proxy, not an action register. An order is linked to an incident when

    via_notification   it was raised for a notification the incident groups (the strongest link the records give)
    on_unit_after      it is a corrective order (PM01) on the incident's unit that started within FOLLOW_DAYS after
                       the incident began (a weaker link, by unit and time)

Order status says what was recorded, not that the action worked: a closed order on a unit that had another incident
afterwards is listed as a lead, not as a failed action.
"""

from collections import defaultdict
from datetime import date, timedelta
from itertools import pairwise

from ..graph import operations as ops

FOLLOW_DAYS = 30  # a corrective order on the unit this soon after an incident is taken as raised for it
CLOSED = ("TECO", "CLSD")  # SAP: technically complete, closed
BANDS = ((30, "under 30 days"), (90, "30 to 90 days"), (180, "90 to 180 days"), (None, "over 180 days"))


def _band(age: int) -> str:
    return next(label for limit, label in BANDS if limit is None or age < limit)


def _d(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def repeats(conn, since: str | None = None, until: str | None = None, days: int = 730) -> dict:
    """Incidents starting in [since, until) (default the `days` to the latest daily report) grouped by unit, with the
    corrective work linked to each and the age of what is still open at the data's latest day."""
    since, until = ops._window(conn, since, until, days)
    today = date.fromisoformat(ops.as_of(conn))
    incidents = conn.execute(
        """
        select i.fuzer_id as id, jsonb_object_agg(g.field, g.value) as f, a.dst as unit, coalesce(l.label, a.dst) as label
        from entity i join golden g using (fuzer_id)
        left join edge a on a.src = i.fuzer_id and a.rel = 'about'
        left join entity_label l on l.fuzer_id = a.dst
        where i.kind = 'incident' and i.retired_at is null and i.merged_into is null
        group by i.fuzer_id, a.dst, l.label
        """).fetchall()
    incidents = sorted((r for r in incidents if since <= str(r["f"].get("start", ""))[:10] < until),
                       key=lambda r: str(r["f"].get("start")))
    ids = [r["id"] for r in incidents]
    via = defaultdict(list)  # incident -> orders raised for its notifications
    for r in conn.execute(
        "select n.src as incident, o.src as wo, n.dst as notification from edge n join edge o on o.dst = n.dst and "
        "o.rel = 'for_notification' where n.rel = 'includes' and n.src = any(%s)", (ids,)):
        via[r["incident"]].append((r["wo"], r["notification"]))
    orders = {o["id"]: o for o in ops._rows(conn, "work_order")}
    on_tag = defaultdict(list)
    for r in conn.execute("select src, dst from edge where rel = 'concerns' and src = any(%s)", (list(orders),)):
        on_tag[r["dst"]].append(r["src"])
    work, linked_to = [], defaultdict(list)
    for inc in incidents:
        start = _d(inc["f"].get("start"))
        seen = set()
        links = [(wo, "via_notification", n) for wo, n in via[inc["id"]]]
        links += [(wo, "on_unit_after", None) for wo in on_tag.get(inc["unit"], [])
                  if orders[wo]["f"].get("order_type") == "PM01" and start and _d(orders[wo]["f"].get("start"))
                  and start <= _d(orders[wo]["f"]["start"]) <= start + timedelta(days=FOLLOW_DAYS)]
        for wo, how, note in links:
            if wo in seen or wo not in orders:
                continue
            seen.add(wo)
            f = orders[wo]["f"]
            status = str(f.get("status") or "unknown")
            opened = _d(f.get("start"))
            closed = status in CLOSED
            finished = _d(f.get("finish")) if closed else None
            age = (today - opened).days if opened and not closed else None
            item = {"id": wo, "order": orders[wo]["label"], "type": f.get("order_type"), "status": status,
                    "open": not closed, "start": str(f.get("start"))[:10] if f.get("start") else None,
                    "finish": finished.isoformat() if finished else None, "age_days": age,
                    "age_band": _band(age) if age is not None else None, "incident": inc["id"], "link": how,
                    "notification": note, "description": f.get("description")}
            work.append(item)
            linked_to[inc["id"]].append(item)
    units = defaultdict(list)
    for inc in incidents:
        units[(inc["unit"], inc["label"])].append(inc)
    rows = []
    for (uid, label), incs in units.items():
        starts = [_d(i["f"].get("start")) for i in incs]
        came_back = []
        for k, inc in enumerate(incs[1:], 1):  # a later incident after work on an earlier one was recorded closed
            for prev in incs[:k]:
                done = [w for w in linked_to[prev["id"]] if not w["open"] and w["finish"] and w["finish"] <= str(starts[k])]
                if done:
                    came_back.append({"incident": inc["id"], "start": str(starts[k]), "after": prev["id"],
                                      "closed_orders": [w["id"] for w in done]})
                    break
        rows.append({"unit_id": uid, "unit": label or "no unit", "incidents": len(incs), "repeat": len(incs) > 1,
                     "days_between": [(b - a).days for a, b in pairwise(starts) if a and b],
                     "lost_oil_sm3": round(sum(ops._num(i["f"].get("lost_oil_sm3")) for i in incs), 1),
                     "came_back_after_closed_work": came_back,
                     "items": [{"id": i["id"], "start": str(i["f"].get("start"))[:10],
                                "description": i["f"].get("description"),
                                "linked_orders": len(linked_to[i["id"]]),
                                "open_orders": sum(1 for w in linked_to[i["id"]] if w["open"])} for i in incs]})
    rows.sort(key=lambda r: (-r["incidents"], -r["lost_oil_sm3"], r["unit"]))
    open_: dict[str, dict] = {}  # one row per order, naming every incident it is linked to
    for w in work:
        if w["open"]:
            open_.setdefault(w["id"], w | {"incidents": []})["incidents"].append(w["incident"])
    open_ = sorted(open_.values(), key=lambda w: -(w["age_days"] or 0))
    return {"since": since, "until": until, "as_of": today.isoformat(), "follow_days": FOLLOW_DAYS,
            "source": "incidents (grouped events, downtime and notifications), SAP notifications and work orders",
            "units": rows, "open_work": open_, "closed_work": [w for w in work if not w["open"]],
            "summary": {"incidents": len(incidents), "units": len(rows),
                        "repeat_units": sum(1 for r in rows if r["repeat"]),
                        "came_back_after_closed_work": sum(len(r["came_back_after_closed_work"]) for r in rows),
                        "linked_orders": len({w["id"] for w in work}), "open_orders": len(open_),
                        "incidents_without_linked_work": sum(1 for i in incidents if not linked_to[i["id"]]),
                        "open_by_age": {label: sum(1 for w in open_ if w["age_band"] == label) for _, label in BANDS}},
            "note": "Linked work is a proxy, not an action register: an order raised for one of the incident's "
                    f"notifications, or a corrective order on its unit within {FOLLOW_DAYS} days. Order status says "
                    "what was recorded, not that the action worked; a repeat after closed work is a lead for review."}
