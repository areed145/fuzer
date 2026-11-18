"""Work that the permit to work system brings (P3.14, #1181): plant left isolated under a permit that has lapsed,
raised as routed work for the ops lead, who stands as the area authority.

A permit past its valid-to time (or marked expired) that is not closed or cancelled, with one or more of its
isolations still applied or verified, means plant is held isolated under a permit nobody holds any more. The next crew
may not know the isolation is there, and the plant can't be safely returned to service until someone either reissues
the permit or reinstates the plant. The `permit_lapsed` detector raises one `permit_lapsed` case per permit, keyed by
the permit number, with its location and isolation points as subjects. It clears when the permit is closed, cancelled
or back in date, or no isolation on it is still in place. A lapsed permit with nothing isolated is paperwork, not work.
Evidence only: what the permit system records, with its times.
"""

from datetime import UTC, datetime

from ..core import detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact
from .readiness import IN_PLACE, _when

DONE = ("closed", "cancelled", "canceled", "completed")


def _type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("permit_lapsed") or exception_type(
        "permit_lapsed", "Permit lapsed with plant still isolated", role="role:ops_lead", audience=("ioc", "field"),
        joins=False,
        description="A permit to work past its valid-to time, not closed, with isolations still in place. Plant is "
                    "held isolated under a permit nobody holds. Reissue the permit if the work goes on, or close it "
                    "and have the isolations removed and the plant reinstated. It clears when the permit is closed "
                    "or back in date, or nothing on it is isolated any more.")


TYPE = _type().name

_ALL = """
    select p.fuzer_id as id, pl.label,
           max(case when g.field = 'permit_type' then g.value #>> '{}' end) as type,
           max(case when g.field = 'state' then g.value #>> '{}' end) as state,
           max(case when g.field = 'valid_from' then g.value #>> '{}' end) as valid_from,
           max(case when g.field = 'valid_to' then g.value #>> '{}' end) as valid_to,
           max(case when g.field = 'issued_to' then g.value #>> '{}' end) as issued_to,
           max(case when g.field = 'description' then g.value #>> '{}' end) as work,
           (select coalesce(jsonb_agg(c.dst order by c.dst), '[]') from edge c
            where c.src = p.fuzer_id and c.rel = 'covers') as locations,
           (select coalesce(jsonb_agg(jsonb_build_object(
                     'id', i.src, 'isolation', il.label, 'point', ip.value #>> '{}', 'method', im.value #>> '{}',
                     'state', ist.value #>> '{}') order by il.label), '[]')
            from edge i join entity_label il on il.fuzer_id = i.src
            left join golden ip on ip.fuzer_id = i.src and ip.field = 'point'
            left join golden im on im.fuzer_id = i.src and im.field = 'method'
            left join golden ist on ist.fuzer_id = i.src and ist.field = 'state'
            where i.rel = 'isolation_for' and i.dst = p.fuzer_id) as isolations
    from entity p
    left join entity_label pl on pl.fuzer_id = p.fuzer_id
    left join golden g on g.fuzer_id = p.fuzer_id
    where p.kind = 'permit' and p.retired_at is null and p.merged_into is null
    group by p.fuzer_id, pl.label
    order by pl.label
"""


def lapsed(conn, now: datetime | None = None) -> list[dict]:
    """Every permit past its valid-to time (or marked expired), not closed, with an isolation still in place."""
    now = now or datetime.now(UTC)
    out = []
    for r in conn.execute(_ALL).fetchall():
        state = (r["state"] or "").strip().lower()
        if state in DONE:
            continue
        end = _when(r["valid_to"])
        if not (state == "expired" or (end is not None and end < now)):
            continue
        held = [i for i in r["isolations"] if (i["state"] or "").lower() in IN_PLACE]
        if not held:
            continue
        names = [x["label"] for x in conn.execute(
            "select label from entity_label where fuzer_id = any(%s) order by label", (list(r["locations"]),))]
        out.append({**dict(r), "held": held, "location_labels": names,
                    "lapsed_hours": round((now - end).total_seconds() / 3600, 1) if end else None})
    return out


def _permits(conn, pack) -> bool:
    return conn.execute("select 1 from edge where rel = 'isolation_for' limit 1").fetchone() is not None


@detectors.register("permit_lapsed", title="A permit lapsed with plant still isolated under it",
                    kind=detectors.RULE, cadence="hourly", raises=(TYPE,),
                    needs={"permits to work with their isolations": _permits},
                    eval="test:tests/ops/test_permit_work.py::test_a_lapsed_permit_with_plant_isolated_is_work_until_closed",
                    found=lambda out: len(out["raised"]))
def watch_permits(conn, by: str = "agent:permits", now: datetime | None = None) -> dict:
    """Each permit past its valid-to time and not closed, with isolations still applied or verified, raised as
    `permit_lapsed` for the ops lead (keyed by the permit); open ones closed, back in date or with nothing isolated
    are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    if isinstance(now, str):
        now = _when(now)
    held_keys = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (TYPE,))]
    found_now = lapsed(conn, now)
    raised, cleared, errors = [], [], []
    for p in found_now:
        where = ", ".join(p["location_labels"]) or "its location"
        items = [fact("Permit", f"{p['label']} ({p['type'] or 'type not recorded'})"), fact("Location", where),
                 fact("Work", p["work"]), fact("Issued to", p["issued_to"]), fact("State", p["state"]),
                 at("Valid to", p["valid_to"]) if p["valid_to"] else fact("Valid to", "not recorded")]
        items += [fact(f"Isolation {i['isolation']}", f"{i['point'] or 'point not recorded'}: "
                                                      f"{i['method'] or 'method not recorded'} ({i['state']})")
                  for i in p["held"]]
        found = Finding("permits.permit_lapsed", DETERMINISTIC,
                        method="Permit to work register: permits past their valid-to time and not closed, read with "
                               "the state of each isolation they hold",
                        start=p["valid_to"], data={"permit": p["label"], "state": p["state"],
                                                   "isolations": [i["isolation"] for i in p["held"]]},
                        items=tuple(items))
        n = len(p["held"])
        title = (f"{p['label']} on {where}: lapsed {p['valid_to'] or ''} with {n} isolation{'s' if n > 1 else ''} "
                 "still in place").replace("  ", " ")
        try:
            x = raise_exception(conn, TYPE, p["label"], title, by, source="ptw",
                                refs=[*p["locations"], p["id"]], priority=0.75, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{p['label']}: {e}")
            continue
        raised.append({"permit": p["label"], "isolations": n, "exception_id": x["id"], "item_id": x["item_id"],
                       "new": x["raised"]})
    for key in set(held_keys) - {p["label"] for p in found_now}:
        clear_exception(conn, TYPE, key, by, reason="the permit is closed or back in date, or nothing is isolated")
        cleared.append(key)
    conn.commit()
    return {"lapsed": len(found_now), "raised": raised, "cleared": cleared, "errors": errors}
