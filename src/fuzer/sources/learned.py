"""The register learns from stewards (P6.5): when people have answered enough values from one source the same way,
the way they answered becomes a proposed rule, with the values it would answer now; accepted, it answers them, and
every like value the source writes after.

What is learned is the shape of an alias (#476). An alias says a value names an entity; compared, letters and digits
only and without case, with each of that entity's keys, the value is often the key with something around it: a
legacy system's prefix (`EQ-30000003` for equipment 30000003), a suffix (`50000036-LEG`), or only other separators
(`20 K 2100 B`). Aliases from one source whose values wrap one kind of key the same way are like decisions; at
`LEARN_AFTER` of them the register proposes the rule (source, key kind, prefix, suffix). A key is any name the entity
goes by: one of its keys, or another source's record key for it (`sap:equipment`, the SAP equipment number). Nothing about the asset type
is assumed: the kinds and the affixes are whatever the stewards' answers show.

    proposals(conn)                         rules the answers support, with what taught each and what it would answer
    accept(conn, source, kind, prefix, suffix, by, reason=None) -> {rule, recorded}
    reject(conn, source, kind, prefix, suffix, by, reason=None)  not proposed again
    withdraw(conn, rule_id, by, reason=None)                     retires the aliases it recorded
    listing(conn)                           decided rules, with how many values each answers
    sync(conn)                              after a rebuild: each accepted rule answers the source's new values

An accepted rule records an ordinary alias per value (created_by `rule:<id>`), so its answers show in the decision log,
can be retired one by one, and resolve on the next rebuild like a steward's, counted in the source's link coverage.
"""

import re

from . import aliases
from ._catalog_common import CatalogError, _audit

LEARN_AFTER = 10  # like answers that make a proposal
BY = "rule:"


def squash(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", str(value or "").lower())


def _affix(value: str, key: str) -> tuple[str, str] | None:
    sv, sk = squash(value), squash(key)
    i = sv.rfind(sk) if sk else -1
    return (sv[:i], sv[i + len(sk):]) if i >= 0 else None


def _shape(keys: list[tuple[str, str]], value: str) -> tuple[str, str, str] | None:
    """(kind, prefix, suffix) of the closest of an entity's names the value wraps: the least around it wins, then its
    keys before sources' record keys, in the order aliases use."""
    order = {k: i for i, k in enumerate(aliases.KEY_ORDER)}
    best = None
    for kind, key in keys:
        if (a := _affix(value, key)) is not None:
            rank = (len(a[0]) + len(a[1]), order.get(kind, len(order)), kind)
            if best is None or rank < best[0]:
                best = (rank, (kind, *a))
    return best[1] if best else None


# Every name an entity goes by: its keys (kind `tag`, `equipment`, ...) and each source's own record key (kind
# `<source>:<record type>`, `sap:equipment`), so a value can be learned as wrapping either.
NAMES = """
    select fuzer_id, kind, key from entity_key where kind <> 'tag_noarea'
    union all
    select fuzer_id, source || ':' || record_type, source_key from crosswalk
"""


def _taught(conn) -> dict[tuple[str, str, str, str], list[dict]]:
    """(source, kind, prefix, suffix) -> the people's current aliases with that shape."""
    rows = conn.execute(
        f"""
        with names as ({NAMES})
        select a.id, a.value, a.source, a.kind as akind, a.key as akey,
               array_agg(array[n.kind, n.key]) filter (where n.kind is not null) as names
        from entity_alias a
        join entity_key k on k.kind = a.kind and k.key = a.key
        left join names n on n.fuzer_id = k.fuzer_id
        where a.retired_at is null and a.source is not null and a.created_by not like 'rule:%%'
        group by a.id
        """).fetchall()
    out: dict[tuple, list[dict]] = {}
    for r in rows:
        if shape := _shape([tuple(x) for x in r["names"] or []] or [(r["akind"], r["akey"])], r["value"]):
            out.setdefault((r["source"], *shape), []).append({"id": r["id"], "value": r["value"], "kind": r["akind"],
                                                             "key": r["akey"]})
    return out


def _unanswered(conn, source: str) -> list[dict]:
    """The values the source's links named nothing for on its last rebuild, without an alias yet."""
    r = conn.execute("select stats->'coverage'->'unresolved' as u from source_match_stat where source = %s "
                     "order by id desc limit 1", (source,)).fetchone()
    have = {a["norm"] for a in conn.execute("select norm from entity_alias where retired_at is null")}
    seen, out = set(), []
    for rel, v in ((r and r["u"]) or {}).items():
        for x in v.get("values") or []:
            norm = " ".join(str(x["value"]).split()).lower()
            if norm not in have and norm not in seen:
                seen.add(norm)
                out.append({"value": x["value"], "rows": x["rows"], "rel": rel})
    return out


def _answers(conn, source: str, kind: str, prefix: str, suffix: str) -> list[dict]:
    """The unanswered values of `source` the rule answers: the value with the affix taken off is one key of `kind`
    on exactly one live entity."""
    index: dict[str, set[tuple[str, str]]] = {}
    for k in conn.execute(f"with names as ({NAMES}) select n.fuzer_id, n.key from names n join entity e using (fuzer_id) "
                          "where n.kind = %s and e.retired_at is null and e.merged_into is null", (kind,)):
        index.setdefault(squash(k["key"]), set()).add((k["fuzer_id"], k["key"]))
    out = []
    for v in _unanswered(conn, source):
        sv = squash(v["value"])
        if len(sv) <= len(prefix) + len(suffix) or not (sv.startswith(prefix) and sv.endswith(suffix)):
            continue
        hits = index.get(sv[len(prefix): len(sv) - len(suffix)], set())
        if len({f for f, _ in hits}) == 1:
            fid, key = next(iter(hits))
            out.append({**v, "fuzer_id": fid, "key": key})
    return out


def _decided(conn) -> dict[tuple[str, str, str, str], dict]:
    return {(r["source"], r["kind"], r["prefix"], r["suffix"]): dict(r) for r in conn.execute("select * from learned_rule")}


def _kind_words(kind: str) -> str:
    source, sep, record = kind.partition(":")
    return f"{source.upper()} {record.replace('_', ' ')} key" if sep else f"{kind.replace('_', ' ')} key"


def _words(source: str, kind: str, prefix: str, suffix: str) -> str:
    around = " and ".join(x for x in (f"{prefix.upper()!r} before" if prefix else "", f"{suffix.upper()!r} after"
                                      if suffix else "") if x) or "only other separators"
    return f"A {source.upper()} value that is a {_kind_words(kind)} with {around} names that item"


def proposals(conn, learn_after: int = LEARN_AFTER) -> list[dict]:
    """Each rule at least `learn_after` like answers support and nobody has decided, with the answers that taught
    it and the values it would answer now (most rows first)."""
    decided = _decided(conn)
    out = []
    for (source, kind, prefix, suffix), taught in sorted(_taught(conn).items()):
        if len(taught) < learn_after or (source, kind, prefix, suffix) in decided:
            continue
        would = sorted(_answers(conn, source, kind, prefix, suffix), key=lambda x: (-x["rows"], x["value"]))
        out.append({"source": source, "kind": kind, "prefix": prefix, "suffix": suffix,
                    "rule": _words(source, kind, prefix, suffix), "taught": len(taught),
                    "examples": sorted(taught, key=lambda x: x["value"])[:5], "taught_by": sorted(t["id"] for t in taught),
                    "would_answer": len(would), "rows": sum(x["rows"] for x in would), "values": would[:50]})
    return sorted(out, key=lambda p: (-p["rows"], -p["taught"], p["source"]))


def _record(conn, rule: dict, by: str) -> int:
    n = 0
    for v in _answers(conn, rule["source"], rule["kind"], rule["prefix"], rule["suffix"]):
        aliases.add(conn, v["value"], v["fuzer_id"], f"{BY}{rule['id']}", source=rule["source"],
                    reason=f"learned rule {rule['id']}: {_words(rule['source'], rule['kind'], rule['prefix'], rule['suffix'])}"
                           f" (accepted by {by})")
        n += 1
    return n


def _decide(conn, source, kind, prefix, suffix, status, by, reason, taught_by=()) -> dict:
    if not by:
        raise CatalogError("a rule decision needs who made it")
    row = dict(conn.execute(
        """
        insert into learned_rule (source, kind, prefix, suffix, status, taught_by, decided_by, reason)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (source, kind, prefix, suffix) do update set status = excluded.status,
            taught_by = excluded.taught_by, decided_by = excluded.decided_by, decided_at = now(), reason = excluded.reason
        returning *
        """, (source, kind, squash(prefix), squash(suffix), status, list(taught_by), by, reason)).fetchone())
    _audit(conn, by, "rule", {"id": row["id"], "name": f"learned:{source}:{kind}:{row['prefix']}|{row['suffix']}"},
           status, new={k: row[k] for k in ("source", "kind", "prefix", "suffix", "status")}, reason=reason)
    return row


def accept(conn, source: str, kind: str, prefix: str, suffix: str, by: str, reason: str | None = None) -> dict:
    """Accept a proposed rule: it answers every like value of the source now, and after each rebuild."""
    key = (source, kind, squash(prefix), squash(suffix))
    taught = _taught(conn).get(key, [])
    if not taught:
        raise CatalogError("no steward answers support that rule")
    rule = _decide(conn, *key, "accepted", by, reason, [t["id"] for t in taught])
    return {"rule": rule, "recorded": _record(conn, rule, by)}


def reject(conn, source: str, kind: str, prefix: str, suffix: str, by: str, reason: str | None = None) -> dict:
    return _decide(conn, source, kind, prefix, suffix, "rejected", by, reason)


def withdraw(conn, rule_id: int, by: str, reason: str | None = None) -> dict:
    """Stop an accepted rule and retire the aliases it recorded; the stewards' own answers stay."""
    row = conn.execute("select * from learned_rule where id = %s and status = 'accepted'", (rule_id,)).fetchone()
    if row is None:
        raise CatalogError(f"no accepted rule {rule_id}")
    retired = 0
    for a in conn.execute("select id from entity_alias where created_by = %s and retired_at is null",
                          (f"{BY}{rule_id}",)).fetchall():
        aliases.retire(conn, a["id"], by, reason=f"learned rule {rule_id} withdrawn")
        retired += 1
    out = _decide(conn, row["source"], row["kind"], row["prefix"], row["suffix"], "withdrawn", by, reason,
                  row["taught_by"])
    return {"rule": out, "retired": retired}


def listing(conn) -> list[dict]:
    """Decided rules, newest first, each with the aliases it has recorded."""
    return [{**dict(r), "rule": _words(r["source"], r["kind"], r["prefix"], r["suffix"])} for r in conn.execute(
        """
        select l.*, (select count(*) from entity_alias a where a.created_by = 'rule:' || l.id and a.retired_at is null)
               as answers
        from learned_rule l order by l.decided_at desc
        """)]


def sync(conn) -> dict:
    """After a rebuild: each accepted rule answers the values its source newly named nothing for."""
    out = {"rules": 0, "recorded": 0}
    for r in conn.execute("select * from learned_rule where status = 'accepted' order by id").fetchall():
        out["rules"] += 1
        out["recorded"] += _record(conn, dict(r), r["decided_by"])
    return out
