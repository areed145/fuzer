"""What a mapping change would do before it is approved, and why a value says what it says (#225).

- preview: run a proposed mapping version and the one in use over the same stored records (all of them, or
  a sample) without writing anything, and diff what they make: entities and links added and removed, fields
  changed, and golden values that would flip because this source wins them now.
- why: a golden field back to the records that asserted it, the mapping version that read each record, the
  run that delivered it, and the rule that chose the winner.
"""

import yaml

from ..core.jsonsafe import plain
from . import catalog, mappings

EXAMPLES = 10


class ImpactError(ValueError):
    pass


def _compose(conn, raw: dict) -> mappings.Mapping:
    """A proposed mapping as it would run: with its dataset's where-fields when it names none of its own."""
    d = next((x for x in catalog.datasets(conn).values()
              if x["source"] == raw.get("source") and x["record_type"] == raw.get("record_type")), None)
    if d and not raw.get("dataset"):
        raw = {**raw, "dataset": d["name"], **({"key": d["key"][0] if len(d["key"]) == 1 else list(d["key"])}
                                               if d["key"] else {})}
    try:
        return mappings.Mapping(raw)
    except mappings.MappingError as e:
        raise ImpactError(f"mapping is not valid: {e}") from e


def _made(m, rec: dict, pack) -> tuple[dict, set]:
    if m is None:
        return {}, set()
    cands, edges = m.extract(rec["source_key"], rec["payload"], pack)
    ents = {(c.kind, c.key): c.fields for c in cands if c.key}
    links = {(e.rel, f"{e.src[0]}:{e.src[1]}", f"{e.dst[0]}:{e.dst[1]}") for e in [*edges, *(e for c in cands for e in c.edges)]}
    return ents, links


def preview(conn, pack, text: str | None = None, version_id: int | None = None, limit: int | None = None) -> dict:
    """The diff between the mapping in use and a proposed one (YAML, or a stored draft) over the stored records."""
    if version_id is not None:
        v = conn.execute("select yaml from mapping_version where id = %s", (version_id,)).fetchone()
        if not v:
            raise ImpactError(f"no mapping version {version_id}")
        text = v["yaml"]
    try:
        raw = yaml.safe_load(text or "")
    except yaml.YAMLError as e:
        raise ImpactError(f"not YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ImpactError("a mapping is a YAML mapping")
    new = _compose(conn, raw)
    old = mappings.load(conn).get((new.source, new.record_type))
    golden = {}
    recs = conn.execute(
        "select source, record_type, source_key, payload from source_record where source = %s and record_type = %s "
        "order by source_key" + (" limit %s" if limit else ""),
        (new.source, new.record_type, *([limit] if limit else []))).fetchall()
    fids = {r["source_key"]: r["fuzer_id"] for r in conn.execute(
        "select source_key, fuzer_id from crosswalk where source = %s and record_type = %s", (new.source, new.record_type))}
    for r in conn.execute("select g.fuzer_id, g.field, g.value from golden g join crosswalk c on c.fuzer_id = g.fuzer_id "
                          "where c.source = %s and c.record_type = %s and g.source = %s",
                          (new.source, new.record_type, new.source)):
        golden[(r["fuzer_id"], r["field"])] = r["value"]

    out = {"mapping": f"{new.source}/{new.record_type}", "records": len(recs), "sampled": bool(limit and len(recs) >= limit),
           "current_version": old.version if old else None,
           "entities": {"added": 0, "removed": 0, "examples": []}, "links": {"added": 0, "removed": 0, "examples": []},
           "fields": {"changed": 0, "examples": []}, "golden_flips": {"count": 0, "examples": []}}

    def ex(section: str, item: dict) -> None:
        bucket = out[section]["examples"]
        if len(bucket) < EXAMPLES:
            bucket.append(item)

    for rec in recs:
        try:
            oe, ol = _made(old, rec, pack)
            ne, nl = _made(new, rec, pack)
        except Exception as e:  # a mapping that breaks on a record: say which, don't fail the preview
            out.setdefault("errors", []).append({"record": rec["source_key"], "error": f"{type(e).__name__}: {e}"})
            continue
        for k in ne.keys() - oe.keys():
            out["entities"]["added"] += 1
            ex("entities", {"record": rec["source_key"], "added": f"{k[0]}:{k[1]}"})
        for k in oe.keys() - ne.keys():
            out["entities"]["removed"] += 1
            ex("entities", {"record": rec["source_key"], "removed": f"{k[0]}:{k[1]}"})
        for link in nl - ol:
            out["links"]["added"] += 1
            ex("links", {"record": rec["source_key"], "added": " ".join(link)})
        for link in ol - nl:
            out["links"]["removed"] += 1
            ex("links", {"record": rec["source_key"], "removed": " ".join(link)})
        fid = fids.get(rec["source_key"])
        for k in ne.keys() & oe.keys():
            a, b = oe[k], ne[k]
            for f in a.keys() | b.keys():
                if a.get(f) == b.get(f):
                    continue
                out["fields"]["changed"] += 1
                ex("fields", {"record": rec["source_key"], "field": f, "was": a.get(f), "now": b.get(f)})
                if fid and (fid, f) in golden and golden[(fid, f)] != b.get(f):
                    out["golden_flips"]["count"] += 1
                    ex("golden_flips", {"entity": fid, "field": f, "golden": golden[(fid, f)], "would_be": b.get(f)})
    return plain(out)


def why(conn, pack, fuzer_id: str, field: str) -> dict:
    """A golden field's lineage: the rule that chose it, and each assertion with its record, mapping version and run."""
    from . import rules

    g = conn.execute("select * from golden where fuzer_id = %s and field = %s", (fuzer_id, field)).fetchone()
    if not g:
        raise ImpactError(f"{fuzer_id} has no {field!r}")
    rule = rules.apply(conn, pack).rule(field)
    from .extract import extract

    current = {(m["source"], m["record_type"]): m for m in catalog.current_mappings(conn)}
    maps = mappings.load(conn)
    said = []  # what each of the entity's records says about the field, re-read with the mapping in use
    for rec in conn.execute(
        "select r.source, r.record_type, r.source_key, r.payload, r.first_seen, r.last_seen from crosswalk c "
        "join source_record r using (source, record_type, source_key) where c.fuzer_id = %s order by r.source",
        (fuzer_id,)):
        for cand in extract(rec, pack, maps)[0]:
            if field in cand.fields:
                said.append({**rec, "value": cand.fields[field]})
    for a in conn.execute("select source, source_key, value, observed_at from assertion where fuzer_id = %s and field = %s",
                          (fuzer_id, field)):  # a reviewer's decision
        said.append({"source": a["source"], "record_type": None, "source_key": a["source_key"], "value": a["value"],
                     "first_seen": a["observed_at"], "last_seen": None})
    out = []
    for a in said:
        m = current.get((a["source"], a["record_type"]))
        run = None
        if a["last_seen"] is not None:  # the load that delivered this version of the record (same transaction)
            run = conn.execute(
                "select id, dataset, trigger, finished_at from source_run where status = 'done' "
                "and mappings ? %s and finished_at <= %s + interval '1 second' "
                "order by (finished_at = %s) desc, finished_at desc limit 1",
                (f"{a['source']}/{a['record_type']}", a["last_seen"], a["last_seen"])).fetchone()
        out.append({"source": a["source"], "record": f"{a['source']}:{a['record_type']}:{a['source_key']}"
                    if a["record_type"] else a["source_key"], "value": a["value"],
                    "wins": a["source"] == g["source"] and a["value"] == g["value"],
                    "record_first_seen": a["first_seen"], "record_last_changed": a["last_seen"],
                    "mapping_version": {"id": m["id"], "author": m["author"], "approved_at": m["approved_at"]} if m else None,
                    "run": dict(run) if run else None})
    return plain({"entity": fuzer_id, "field": field, "value": g["value"], "source": g["source"], "rule": g["rule"],
                  "priority": rule.get("priority"), "review_on_conflict": rule.get("review_on_conflict", False),
                  "assertions": out})
