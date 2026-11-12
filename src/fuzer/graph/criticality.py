"""Missing criticality is the data steward's work: one case per asset whose consequence nobody has rated.

Criticality (the SAP ABC indicator, in the golden record) ranks work by consequence and decides where a person must
look before anything automatic happens (the human gates, P5.10). An asset without one is not treated as the most or
the least critical: it is a gap a person fills. Each asset (a tag that is not part of another, or a well) whose
criticality is not assessed is an open `criticality_not_assessed` case in the data steward's queue, keyed by the
asset, so one asset is one case however often the register is rebuilt. Setting a criticality (a fix on Master data,
or the source system's next load) clears it at the next rebuild. Until then, values and gates that read criticality
say "not assessed".

A register that rates no asset at all raises nothing: there is nothing to say one asset is missing what the others
carry, and its source is the gap.

    missing(conn)   the assets with no criticality
    sync(conn)      one open case per asset in `missing`, the rest cleared
"""

from ..core.exceptions import clear_exception, exception_type, open_keys, raise_exception
from ..core.findings import EXTERNAL, Finding, asset, fact, page

BY = "stewardship"
NOT_ASSESSED = "not assessed"  # what a value or gate says where criticality is missing

TYPE = exception_type("criticality_not_assessed", "Criticality not assessed", role="role:data_steward", joins=False,
                      description="An asset has no criticality, so its work can't be ranked by consequence; "
                                  "rate it.")

ASSETS = """
select e.fuzer_id, e.kind from entity e
where e.merged_into is null and e.retired_at is null
  and (e.kind = 'well' or (e.kind = 'tag' and not exists (
        select 1 from edge p join entity a on a.fuzer_id = p.dst and a.kind in ('tag', 'well')
        where p.src = e.fuzer_id and p.rel = 'part_of')))
"""


def label(value) -> str:
    """A criticality as a person reads it: the indicator, or "not assessed"."""
    v = str(value or "").strip().upper()
    return v or NOT_ASSESSED


def missing(conn) -> list[dict]:
    """The assets with no criticality, by label; none when the register rates no asset at all."""
    if not conn.execute("select 1 from golden where field = 'criticality' limit 1").fetchone():
        return []
    return [dict(r) for r in conn.execute(
        f"""
        with asset as ({ASSETS})
        select a.fuzer_id, a.kind, coalesce(l.label, a.fuzer_id) as label, d.value #>> '{{}}' as description
        from asset a
        left join entity_label l on l.fuzer_id = a.fuzer_id
        left join golden d on d.fuzer_id = a.fuzer_id and d.field = 'description'
        where not exists (select 1 from golden g where g.fuzer_id = a.fuzer_id and g.field = 'criticality'
                          and coalesce(g.value #>> '{{}}', '') <> '')
        order by 3
        """)]


def title(a: dict) -> str:
    what = f" ({a['description']})" if a.get("description") else ""
    return f"{a['label']}{what}: criticality {NOT_ASSESSED}"


def finding(a: dict) -> Finding:
    return Finding("stewardship.criticality", EXTERNAL, method=TYPE.description, items=(
        asset("Asset", a["fuzer_id"]),
        fact("Criticality", NOT_ASSESSED),
        page("Set it", "#/masterdata?tab=gaps", "Set criticality on Master data"),
    ), data={"fuzer_id": a["fuzer_id"]})


def sync(conn, by: str = BY) -> dict:
    """One open case per asset with no criticality; a case whose asset now has one is cleared."""
    out = {"raised": 0, "refreshed": 0, "cleared": 0}
    live = set()
    for a in missing(conn):
        live.add(a["fuzer_id"])
        row = raise_exception(conn, TYPE.name, a["fuzer_id"], title(a)[:300], by, refs=[a["fuzer_id"]],
                              source=BY, finding=finding(a))
        out["raised" if row.get("raised") else "refreshed"] += 1
    for key in open_keys(conn, TYPE.name) - live:
        rated = conn.execute("select 1 from golden where fuzer_id = %s and field = 'criticality'", (key,)).fetchone()
        clear_exception(conn, TYPE.name, key, by,
                        reason="a criticality was set" if rated else "it is no longer an asset in the register")
        out["cleared"] += 1
    return out
