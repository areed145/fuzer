"""Register gaps a steward works (#476), each with where to fix it:

    completeness(conn)   fields most entities of a kind have and some lack ("serial on 92% of pumps; 14 lack it")
    consistency(conn)    one value written several ways ("Flowserve", "FLOWSERVE", "Flowserve Corp."), the usual first
    conflicts(conn)      open conflict reviews: sources disagree on a value and the field rule asks a person
    set_values(conn, field, value, fuzer_ids, by, reason)    one value for those entities (kept as a review choice)
    undo(conn, batch, by, reason)                            put the values back

Expectations come from the register itself (a field most of a kind carry), so nothing has to be configured first.
"""

import re
import uuid
from collections import Counter, defaultdict

from psycopg.types.json import Jsonb

from ..sources._catalog_common import CatalogError, _audit
from .quality import LIVE_E

SUFFIXES = re.compile(r"\b(inc|incorporated|ltd|limited|llc|plc|corp|corporation|co|company|gmbh|ag|sa|bv)\b")


def _norm(value: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", str(value).lower())
    s = SUFFIXES.sub(" ", s)
    return "".join(s.split())


def completeness(conn, min_share: float = 0.8, min_entities: int = 10, examples: int = 20) -> list[dict]:
    """Per kind, the fields at least `min_share` of its entities have, with the ones that lack them."""
    kinds = {r["kind"]: r["n"] for r in conn.execute(
        f"select kind, count(*) as n from entity e where {LIVE_E} group by kind having count(*) >= %s",
        (min_entities,))}
    out = []
    for r in conn.execute(
            f"select e.kind, g.field, count(*) as n from golden g join entity e using (fuzer_id) where {LIVE_E} "
            "and e.kind = any(%s) and g.rule <> 'derived' group by 1, 2", (list(kinds),)):
        total = kinds[r["kind"]]
        share = r["n"] / total
        if min_share <= share < 1:
            out.append({"kind": r["kind"], "field": r["field"], "have": r["n"], "total": total,
                        "missing": total - r["n"], "share": round(share, 3)})
    out.sort(key=lambda x: (-x["share"], -x["missing"]))
    for g in out[:50]:
        g["examples"] = [dict(x) for x in conn.execute(
            f"""
            select e.fuzer_id, l.label from entity e left join entity_label l using (fuzer_id)
            where {LIVE_E} and e.kind = %s
              and not exists (select 1 from golden g where g.fuzer_id = e.fuzer_id and g.field = %s)
            order by l.label nulls last limit %s
            """, (g["kind"], g["field"], examples))]
    return out[:50]


def consistency(conn, max_groups: int = 100) -> list[dict]:
    """Text values written more than one way within a field (case, spacing, punctuation, Inc/Ltd/Corp), most
    entities first. Each group lists the spellings by how many entities use them; the first is the usual one."""
    by_field: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    ids: dict[tuple, list[str]] = defaultdict(list)
    for r in conn.execute(
            f"select g.field, g.value #>> '{{}}' as v, g.fuzer_id from golden g join entity e using (fuzer_id) "
            f"where {LIVE_E} and jsonb_typeof(g.value) = 'string' and g.rule <> 'derived'"):
        n = _norm(r["v"])
        if len(n) < 2:
            continue
        by_field[r["field"]][n][r["v"]] += 1
        ids[(r["field"], r["v"])].append(r["fuzer_id"])
    out = []
    for field, groups in by_field.items():
        if len(groups) > 0.5 * sum(sum(c.values()) for c in groups.values()):
            continue  # mostly unique values (names, serials): spellings there aren't a vocabulary
        for spellings in groups.values():
            if len(spellings) < 2:
                continue
            ranked = spellings.most_common()
            out.append({"field": field, "usual": ranked[0][0], "entities": sum(spellings.values()),
                        "spellings": [{"value": v, "entities": n, "fuzer_ids": ids[(field, v)][:200]}
                                      for v, n in ranked]})
    out.sort(key=lambda x: (-x["entities"], x["field"], x["usual"]))
    return out[:max_groups]


def conflicts(conn, limit: int = 200) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """
        select i.id, i.payload->>'fuzer_id' as fuzer_id, i.payload->>'field' as field, i.payload->'chosen' as chosen,
               i.payload->'values' as "values", l.label, i.created_at
        from review_item i left join entity_label l on l.fuzer_id = i.payload->>'fuzer_id'
        where i.kind = 'conflict' and i.status = 'open' order by i.created_at desc, i.id limit %s
        """, (limit,))]


def set_values(conn, field: str, value, fuzer_ids: list[str], by: str, reason: str | None = None) -> dict:
    """`value` for `field` on each entity, as a reviewer's choice (it wins on every rebuild); undoable as a batch."""
    fuzer_ids = list(dict.fromkeys(fuzer_ids))
    if not field or value in (None, "") or not fuzer_ids:
        raise CatalogError("a fix needs a field, a value and at least one entity")
    live = {r["fuzer_id"] for r in conn.execute(
        f"select fuzer_id from entity e where {LIVE_E} and fuzer_id = any(%s)", (fuzer_ids,))}
    if missing := [f for f in fuzer_ids if f not in live]:
        raise CatalogError(f"no current entity {missing[0]}" + (f" (and {len(missing) - 1} more)" if len(missing) > 1
                                                                 else ""))
    derived = conn.execute("select fuzer_id from golden where field = %s and rule = 'derived' and fuzer_id = any(%s) "
                           "limit 1", (field, fuzer_ids)).fetchone()
    if derived:
        raise CatalogError(f"{field} on {derived['fuzer_id']} is derived from other records; fix it there")
    batch = f"fix-{uuid.uuid4().hex[:10]}"
    old = {r["fuzer_id"]: r for r in conn.execute(
        "select fuzer_id, value, source, rule from golden where field = %s and fuzer_id = any(%s)", (field, fuzer_ids))}
    changed = 0
    for fid in fuzer_ids:
        o = old.get(fid)
        if o and o["value"] == value:
            continue
        conn.execute("insert into steward_fix (batch, fuzer_id, field, old_value, old_source, old_rule, new_value, "
                     "created_by, reason) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                     (batch, fid, field, Jsonb(o["value"]) if o else None, o and o["source"], o and o["rule"],
                      Jsonb(value), by, reason))
        conn.execute("delete from assertion where fuzer_id = %s and field = %s and source = 'review'", (fid, field))
        conn.execute("insert into assertion (fuzer_id, field, value, source, source_key) values (%s, %s, %s, 'review', "
                     "%s)", (fid, field, Jsonb(value), by))
        conn.execute("insert into golden (fuzer_id, field, value, source, rule) values (%s, %s, %s, 'review', 'review') "
                     "on conflict (fuzer_id, field) do update set value = excluded.value, source = excluded.source, "
                     "rule = excluded.rule", (fid, field, Jsonb(value)))
        changed += 1
    if not changed:
        raise CatalogError(f"each of them already has {field} {value!r}")
    _audit(conn, by, "fix", {"name": batch}, "create", new={"field": field, "value": value, "entities": changed},
           reason=reason)
    return {"batch": batch, "field": field, "value": value, "changed": changed}


def undo(conn, batch: str, by: str, reason: str | None = None) -> dict:
    rows = conn.execute("update steward_fix set undone_at = now(), undone_by = %s where batch = %s and undone_at is null "
                        "returning *", (by, batch)).fetchall()
    if not rows:
        raise CatalogError(f"no current fix {batch}")
    for r in rows:
        conn.execute("delete from assertion where fuzer_id = %s and field = %s and source = 'review' and value = %s",
                     (r["fuzer_id"], r["field"], Jsonb(r["new_value"])))
        if r["old_source"] == "review":
            conn.execute("insert into assertion (fuzer_id, field, value, source, source_key) values "
                         "(%s, %s, %s, 'review', %s)", (r["fuzer_id"], r["field"], Jsonb(r["old_value"]), by))
        if r["old_value"] is None:
            conn.execute("delete from golden where fuzer_id = %s and field = %s", (r["fuzer_id"], r["field"]))
        else:
            conn.execute("update golden set value = %s, source = %s, rule = %s where fuzer_id = %s and field = %s",
                         (Jsonb(r["old_value"]), r["old_source"], r["old_rule"], r["fuzer_id"], r["field"]))
    _audit(conn, by, "fix", {"name": batch}, "delete", old={"field": rows[0]["field"], "entities": len(rows)},
           reason=reason)
    return {"undone": batch, "entities": len(rows)}


def listing(conn, current: bool = True, limit: int = 200) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """
        select batch, field, new_value #>> '{}' as value, count(*) as entities, min(created_by) as by,
               min(created_at) as at, min(reason) as reason, min(undone_at) as undone_at, min(undone_by) as undone_by
        from steward_fix group by batch, field, new_value having (not %s or bool_and(undone_at is null))
        order by min(created_at) desc limit %s
        """, (current, limit))]
