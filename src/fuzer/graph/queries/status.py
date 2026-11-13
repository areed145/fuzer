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
