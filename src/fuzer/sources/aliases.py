"""Aliases a steward records (#476): a value some system writes that names nothing in the register (`FLARE-TIP-9`,
a legacy tag style, a planner's shorthand) recorded as another name for an entity, so it matches from now on.

    add(conn, value, fuzer_id, by, source=None, reason=None)    remember it (audited, like the source catalog)
    retire(conn, alias_id, by, reason=None)                      undo it
    lookup(conn, value) -> (kind, key) | None                    what `exceptions.match` and resolvers consult
    apply(pack, conn) -> pack                                    the pack's aliases with these laid over them

An alias points at the entity by one of its keys (a tag, a well, an equipment number), so it survives rebuilds that
re-number Fuzer IDs. The pack's aliases stay the default; one recorded here wins over it. Values are matched without
case. A ref written `kind:value` is matched on its value too, so an alias for `FLARE-TIP-9` also matches
`tag:FLARE-TIP-9` from another system.
"""

import dataclasses

from ._catalog_common import CatalogError, _audit

KEY_ORDER = ("tag", "well", "equipment", "floc", "area", "facility", "document", "point")


def _norm(value: str) -> str:
    return " ".join(str(value or "").split()).lower()


def bare(ref: str) -> str:
    """The value in a ref: `tag:FLARE-TIP-9` -> `FLARE-TIP-9`; a `source/key` ref or a plain value as written."""
    kind, sep, rest = str(ref).partition(":")
    return rest if sep and kind.isidentifier() and rest else str(ref)


def _target(conn, fuzer_id: str) -> tuple[str, str]:
    keys = {r["kind"]: r["key"] for r in conn.execute(
        "select kind, min(key) as key from entity_key where fuzer_id = %s and kind <> 'tag_noarea' group by kind",
        (fuzer_id,))}
    if not keys:
        raise CatalogError(f"{fuzer_id} has no key to point an alias at")
    kind = next((k for k in KEY_ORDER if k in keys), min(keys))
    return kind, keys[kind]


def add(conn, value: str, fuzer_id: str, by: str, source: str | None = None, reason: str | None = None) -> dict:
    """Record `value` as another name for the entity (one alias per value: a new one replaces the old)."""
    value = bare(str(value or "").strip())
    if not value:
        raise CatalogError("an alias needs a value")
    if not conn.execute("select 1 from entity where fuzer_id = %s and retired_at is null", (fuzer_id,)).fetchone():
        raise CatalogError(f"no entity {fuzer_id}")
    kind, key = _target(conn, fuzer_id)
    old = conn.execute("select * from entity_alias where norm = %s and retired_at is null", (_norm(value),)).fetchone()
    if old and (old["kind"], old["key"]) == (kind, key):
        return dict(old)
    if old:
        retire(conn, old["id"], by, reason=f"replaced: {reason or 'now names ' + key}")
    row = dict(conn.execute(
        """
        insert into entity_alias (value, norm, kind, key, fuzer_id, source, created_by, reason)
        values (%s, %s, %s, %s, %s, %s, %s, %s) returning *
        """, (value, _norm(value), kind, key, fuzer_id, source, by, reason)).fetchone())
    _audit(conn, by, "alias", {"id": row["id"], "name": value}, "create", new={"value": value, "kind": kind, "key": key,
                                                                              "source": source}, reason=reason)
    return row


def retire(conn, alias_id: int, by: str, reason: str | None = None) -> dict:
    row = conn.execute("update entity_alias set retired_at = now(), retired_by = %s where id = %s and retired_at is null "
                       "returning *", (by, alias_id)).fetchone()
    if row is None:
        raise CatalogError(f"no current alias {alias_id}")
    _audit(conn, by, "alias", {"id": row["id"], "name": row["value"]}, "delete",
           old={"value": row["value"], "kind": row["kind"], "key": row["key"]}, reason=reason)
    return dict(row)


def listing(conn, current: bool = True, q: str | None = None, limit: int = 200) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """
        select * from entity_alias where (not %(c)s or retired_at is null)
          and (%(q)s::text is null or norm like '%%' || lower(%(q)s) || '%%' or key ilike '%%' || %(q)s || '%%')
        order by created_at desc limit %(n)s
        """, {"c": current, "q": q or None, "n": limit})]


def lookup(conn, ref: str) -> list[str]:
    """Fuzer IDs a ref names through a recorded alias (as written, or its value after `kind:`)."""
    norms = list(dict.fromkeys([_norm(ref), _norm(bare(ref))]))
    return [r["fuzer_id"] for r in conn.execute(
        """
        select distinct k.fuzer_id from entity_alias a join entity_key k on k.kind = a.kind and k.key = a.key
        where a.retired_at is null and a.norm = any(%s)
        """, (norms,))]


def apply(conn, pack):
    """The pack with recorded aliases laid over its own (what the resolvers use on a rebuild)."""
    rows = conn.execute("select norm, kind, key from entity_alias where retired_at is null").fetchall()
    if not rows:
        return pack
    return dataclasses.replace(pack, aliases={**pack.aliases, **{r["norm"]: (r["kind"], r["key"]) for r in rows}})
