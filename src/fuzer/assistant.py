"""Troubleshooting brief: everything Fuzer knows that helps with a problem on one unit, in one call.

For an incident (or a tag / equipment, using its latest incident if any): what happened, similar past
incidents and how they ended, reviewed lessons for its model or class, the manual / guide sections
that cover its failure modes, maintenance history, downtime, spares in stock, and suggested checks.
Every item carries the entity it came from, so an agent or engineer can cite and open it. Nothing
here acts on the plant. Process values come from the configured time-series provider (PI Web API,
ADX or synthetic; timeseries.py), summarised per point with how the lead-in differs from before.
"""

from . import knowledge, lessons, queries
from .pack import Pack


def brief(conn, pack: Pack, fuzer_id: str) -> dict:
    ent = queries.entity(conn, fuzer_id, per_group=20)
    if ent is None:
        return {"error": "unknown entity"}
    incident, unit = None, fuzer_id
    if ent["kind"] == "incident":
        incident = fuzer_id
        unit = next((x["other"] for x in ent["edges"] if x["rel"] == "about" and x["direction"] == "out"), fuzer_id)
    else:
        latest = queries.incidents(conn, fuzer_id, limit=1)["items"]
        incident = latest[0]["id"] if latest else None
    inc = queries.entity(conn, incident, per_group=5)["golden"] if incident else {}
    modes = (inc.get("failure_modes") or {}).get("value") or []

    scope = knowledge.scope(conn, unit)
    scope_keys = {(r["kind"], r["key"]) for r in conn.execute(
        "select kind, key from entity_key where fuzer_id = any(%s) and kind in ('equipment_model', 'equipment_class')",
        (list(scope),))}
    approved = [lsn for lsn in lessons.listing(conn, "approved", 500)
                if (lsn["scope_kind"], lsn["scope_key"]) in scope_keys and (not modes or lsn["failure_mode"] in modes)]
    drafts = [lsn for lsn in lessons.listing(conn, "suggested", 500)
              if (lsn["scope_kind"], lsn["scope_key"]) in scope_keys and (not modes or lsn["failure_mode"] in modes)]
    reading = knowledge.for_incident(conn, incident, 5) if incident else []
    maintenance = queries.maintenance(conn, unit, limit=5)
    checks = [{"step": line.lstrip("- ").split(" (")[0], "from": f"lesson {lsn['id']} (approved)"}
              for lsn in approved for line in (lsn["text"] or "").splitlines() if line.startswith("- ")]
    checks += [{"step": f"Read {r['document_label']} {r['heading']}" + (f", page {r['page']}" if r["page"] else ""),
                "from": r["id"]} for r in reading[:3]]

    return {
        "about": {"id": unit, "label": queries.entity(conn, unit, per_group=1)["label"]},
        "incident": ({"id": incident, **{k: (inc.get(k) or {}).get("value") for k in
                                         ("description", "start", "end", "lost_oil_sm3", "failure_modes", "event_types")},
                      "timeline": queries.incident_timeline(conn, incident)} if incident else None),
        "similar_incidents": queries.similar_incidents(conn, incident, 5) if incident else [],
        "lessons": {"approved": [{k: lsn[k] for k in ("id", "title", "text", "stats")} for lsn in approved],
                    "drafts_not_yet_reviewed": [{k: lsn[k] for k in ("id", "title", "stats")} for lsn in drafts]},
        "suggested_reading": reading,
        "documents": knowledge.documents_for(conn, unit)[:8],
        "maintenance": {"failure_modes": maintenance["failure_modes"][:5], "recent": maintenance["recent"]},
        "downtime": {k: v for k, v in queries.downtime(conn, unit, limit=3).items()},
        "spares": [{k: p[k] for k in ("fuzer_id", "label", "description", "mpn", "stock", "bin")}
                   for p in queries.spares(conn, unit)],
        "suggested_checks": checks[:8],
        "process_values": _values(conn, incident or unit),
    }


def _values(conn, fuzer_id: str) -> dict:
    from . import timeseries
    out = timeseries.around(conn, fuzer_id, hours_before=24, hours_after=6)
    return {k: v for k, v in out.items() if k != "series"} | {
        "series": [{k: v for k, v in s.items() if k != "values"} for s in out.get("series", [])],
        "note": "synthetic values: no PI / ADX connection is configured (FUZER_TIMESERIES)"
        if out.get("provider") == "synthetic" else None}
