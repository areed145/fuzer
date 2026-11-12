"""Critical spares coverage (P4.2g-2, #349): which units would wait on a part if they failed today.

For each unit in scope (a tag, its installed equipment, everything under an area or facility, or every unit of a class
or model) the spare parts on its equipment's bill of materials against stock on hand, ranked by the unit's criticality
(A first) and then by how often it has failed. A unit whose equipment has no bill of materials is unknown, never
covered; a unit nobody rated for criticality ranks last and says "not assessed".

    uncovered   at least one part on its bill of materials has none in stock
    covered     every part has stock
    unknown     no bill of materials to check

A stock count is not a reservation, and minimum stock and lead times are not in the records, so "covered" means one
or more on the shelf today, not enough. Ordering stays with supply chain.
"""

from datetime import date, timedelta

from ..graph import operations as ops
from .calcs import CalcError, _r, result

FAILURE_DAYS = 730  # the failure history that ranks units of the same criticality
RANK = {"A": 0, "B": 1, "C": 2}


def _units(conn, fid: str) -> list[str]:
    """The units in scope (tags that are not part of another tag, as criticality rates them): the tag itself, the tag a piece of equipment is installed at, the tags of a class or model's
    units, or every tag under an area or facility."""
    e = conn.execute("select kind from entity where fuzer_id = %s", (fid,)).fetchone()
    if e is None:
        raise CalcError("unknown entity")
    rows = conn.execute(
        """
        with recursive scope(id) as (
            select %(id)s::text
            union select src from edge where rel in ('classified_as', 'instance_of') and dst = %(id)s
            union select p.src from edge p join scope s on p.dst = s.id where p.rel = 'part_of'
        )
        select distinct coalesce(i.dst, s.id) as tag from scope s
        left join edge i on i.src = s.id and i.rel = 'installed_at' and i.valid_to is null
        join entity t on t.fuzer_id = coalesce(i.dst, s.id) and t.kind = 'tag'
        where not exists (select 1 from edge p join entity a on a.fuzer_id = p.dst and a.kind in ('tag', 'well')
                          where p.src = t.fuzer_id and p.rel = 'part_of')  -- a unit, not an instrument on one
        """, {"id": fid}).fetchall()
    return [r["tag"] for r in rows]


def rank(units: list[dict]) -> list[dict]:
    """Units ranked for attention: uncovered before unknown before covered, then criticality (A first, not assessed
    last), then the most failures."""
    status = {"uncovered": 0, "unknown": 1, "covered": 2}
    return sorted(units, key=lambda u: (status[u["status"]], RANK.get(u["criticality"], 3), -u["failures"], u["unit"]))


def coverage(conn, fid: str, until: str | None = None, limit: int = 20) -> dict:
    """The spares on each unit's bill of materials against stock, ranked by criticality and failure history. Flags
    `critical_stock_out` when an A-critical unit has a part with none in stock."""
    tags = _units(conn, fid)
    if not tags:
        raise CalcError("no tagged units in that scope to check spares for")
    end = until or (date.fromisoformat(ops.as_of(conn)) + timedelta(days=1)).isoformat()
    since = (date.fromisoformat(end[:10]) - timedelta(days=FAILURE_DAYS)).isoformat()
    rows = conn.execute(
        """
        select t.id as tag, coalesce(l.label, t.id) as label, upper(nullif(c.value #>> '{}', '')) as criticality,
               (select coalesce(jsonb_agg(distinct jsonb_build_object(
                    'id', b.dst, 'label', coalesce(ml.label, b.dst), 'stock', (s.value #>> '{}')::numeric)), '[]')
                from edge i join edge b on b.src = i.src and b.rel = 'uses_part'
                left join entity_label ml on ml.fuzer_id = b.dst
                left join golden s on s.fuzer_id = b.dst and s.field = 'stock'
                where i.dst = t.id and i.rel = 'installed_at' and i.valid_to is null) as parts,
               (select count(distinct n.fuzer_id) from edge x
                join entity n on n.fuzer_id = x.src and n.kind = 'notification'
                join golden g on g.fuzer_id = n.fuzer_id and g.field = 'created'
                where x.rel = 'concerns' and g.value #>> '{}' >= %(since)s and g.value #>> '{}' < %(end)s
                  and (x.dst = t.id or x.dst in (select src from edge where dst = t.id and rel = 'installed_at'
                                                 and valid_to is null))) as failures
        from unnest(%(tags)s::text[]) as t(id)
        left join entity_label l on l.fuzer_id = t.id
        left join golden c on c.fuzer_id = t.id and c.field = 'criticality'
        """, {"tags": tags, "since": since, "end": end}).fetchall()
    units = []
    for r in rows:
        parts = r["parts"] or []
        out = [p for p in parts if p["stock"] is not None and float(p["stock"]) <= 0]
        units.append({"unit": r["label"], "id": r["tag"], "criticality": r["criticality"] or "not assessed",
                      "failures": r["failures"], "parts": len(parts),
                      "out_of_stock": [p["label"] for p in out],
                      "unknown_stock": sum(1 for p in parts if p["stock"] is None),
                      "status": "unknown" if not parts else "uncovered" if out else "covered"})
    units = rank(units)
    checked = [u for u in units if u["status"] != "unknown"]
    critical_out = [u for u in units if u["status"] == "uncovered" and u["criticality"] == "A"]
    counts = {s: sum(1 for u in units if u["status"] == s) for s in ("uncovered", "covered", "unknown")}
    return result(len(critical_out), 0, "critical_stock_out" if critical_out else None,
                  f"bill of materials of each unit's installed equipment against stock on hand, {len(checked)} of "
                  f"{len(units)} units with a bill of materials; ranked by criticality, then failure notifications "
                  f"since {since}", [fid, *[u["id"] for u in units[:5]]], unit="A-critical units with a part out "
                  "of stock", units=units[:limit], considered=len(units), **counts,
                  note="One or more on the shelf, not enough: minimum stock and lead times are not in the records. "
                       "A stock count is not a reservation; ordering stays with supply chain.",
                  failure_days=FAILURE_DAYS, uncovered_share_pct=_r(counts["uncovered"] / len(checked) * 100, 1)
                  if checked else None)
