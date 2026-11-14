"""How an entity is doing right now, in a few facts: the answer a person wants before reading the page."""

from .downtime import downtime, incidents


def status(conn, fuzer_id: str) -> dict:
    """The newest downtime on the entity (open when it has no end), its newest incident and, for something a well
    test measured, the latest test's values. Each is None when there is none; nothing here says a unit is running,
    only that no downtime is open."""
    down = (downtime(conn, fuzer_id, limit=1)["recent"] or [None])[0]
    found = incidents(conn, fuzer_id, limit=1)
    incident = (found["items"] or [None])[0]
    rows = conn.execute(
        """
        select m.at, m.quantity, m.value, m.unit from measurement m
        where m.fuzer_id = %(id)s and m.method = 'well test'
          and m.record = (select record from measurement where fuzer_id = %(id)s and method = 'well test'
                          order by at desc, record limit 1)
        order by m.quantity
        """, {"id": fuzer_id}).fetchall()
    return {
        "downtime": down and {"id": down["id"], "start": down["start"], "end": down["end"], "reason": down["reason"],
                              "open": not down["end"]},
        "incident": incident and {"id": incident["id"], "start": incident.get("start"), "description": incident.get("description"),
                                  "total": found["total"]},
        "well_test": {"at": rows[0]["at"].isoformat()[:10],
                      "values": [{"quantity": r["quantity"], "value": r["value"], "unit": r["unit"]} for r in rows]}
        if rows else None,
    }


_FACTS = ("description", "manufacturer", "model", "serial", "criticality", "template")


def _golden(conn, ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {i: {} for i in ids}
    for r in conn.execute("select fuzer_id, field, value #>> '{}' as v from golden where fuzer_id = any(%s) and field = any(%s)",
                          (ids, list(_FACTS))):
        out[r["fuzer_id"]][r["field"]] = r["v"]
    return out


def _one(conn, sql: str, fuzer_id: str) -> dict | None:
    return conn.execute(sql, (fuzer_id,)).fetchone()


def profile(conn, fuzer_id: str) -> dict:
    """What this asset is, for the top of its page: maker, model and serial (for a position, of the unit installed there
    now, and since when), its class and criticality, the model's page, and where it sits (each parent up to the site).
    Every value is the register's golden value; a fact no source states is left out, not guessed."""
    from .. import knowledge

    installed = _one(conn, """
        select e.src as id, l.label, e.valid_from from edge e left join entity_label l on l.fuzer_id = e.src
        where e.rel = 'installed_at' and e.dst = %s and e.valid_to is null order by e.valid_from desc nulls last limit 1
        """, fuzer_id)
    position = _one(conn, """
        select e.dst as id, l.label, e.valid_from from edge e left join entity_label l on l.fuzer_id = e.dst
        where e.rel = 'installed_at' and e.src = %s and e.valid_to is null order by e.valid_from desc nulls last limit 1
        """, fuzer_id)
    unit = installed["id"] if installed else fuzer_id  # the physical unit: what's installed here, or this itself
    g = _golden(conn, list({fuzer_id, unit}))
    own, phys = g[fuzer_id], g[unit]
    model = _one(conn, """
        select e.dst as id, l.label from edge e left join entity_label l on l.fuzer_id = e.dst
        where e.rel = 'instance_of' and e.src = %s limit 1
        """, unit)
    klass = _one(conn, """
        select e.dst as id, l.label, gd.value #>> '{}' as description from edge e
        left join entity_label l on l.fuzer_id = e.dst
        left join golden gd on gd.fuzer_id = e.dst and gd.field = 'description'
        where e.rel = 'classified_as' and e.src = %s limit 1
        """, fuzer_id) or _one(conn, """
        select e.dst as id, l.label, gd.value #>> '{}' as description from edge e
        left join entity_label l on l.fuzer_id = e.dst
        left join golden gd on gd.fuzer_id = e.dst and gd.field = 'description'
        where e.rel = 'classified_as' and e.src = %s limit 1
        """, unit)
    # Where it sits: each part_of parent in turn (an instrument's unit, then its area, then the site), at most five up.
    where: list[dict] = []
    at, seen = (position["id"] if position else fuzer_id), {fuzer_id}
    for _ in range(5):
        up = _one(conn, """
            select e.dst as id, x.kind, l.label, gd.value #>> '{}' as description from edge e
            join entity x on x.fuzer_id = e.dst left join entity_label l on l.fuzer_id = e.dst
            left join golden gd on gd.fuzer_id = e.dst and gd.field = 'description'
            where e.rel = 'part_of' and e.src = %s and e.valid_to is null order by x.kind limit 1
            """, at)
        if not up or up["id"] in seen:
            break
        where.append(up)
        seen.add(up["id"])
        at = up["id"]
    docs = knowledge.documents_for(conn, fuzer_id)
    return {
        "description": own.get("description") or phys.get("description"),
        "manufacturer": phys.get("manufacturer") or own.get("manufacturer"),
        "model": phys.get("model") or own.get("model"),
        "serial": phys.get("serial") or own.get("serial"),
        "criticality": own.get("criticality"),
        "template": own.get("template") or phys.get("template"),
        "equipment_model": model,
        "equipment_class": klass,
        "installed": installed and {"id": installed["id"], "label": installed["label"],
                                    "since": installed["valid_from"].isoformat()[:10] if installed["valid_from"] else None},
        "position": position and {"id": position["id"], "label": position["label"],
                                  "since": position["valid_from"].isoformat()[:10] if position["valid_from"] else None},
        "location": where,
        "manuals": [d for d in docs if d.get("doc_type") in ("oem_manual", "datasheet")][:3] or docs[:2],
    }
