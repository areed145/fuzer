"""The Ontology page's model (#241): what is declared (core.ontology) next to what the register holds
(queries.ontology), and renaming a kind across the register and the mappings that make it."""

import re

from ..core import ontology as declared_model
from ..core.jsonsafe import plain
from ..core.ontology import OntologyError
from ..sources import catalog
from .queries import ontology as observed_model


def model(conn) -> dict:
    """Observed kinds and links with what the ontology says about each, the declared model with counts, and
    what needs a definition: kinds and link types the register has that nobody declared, and links between
    kinds their link type doesn't allow."""
    obs = observed_model(conn)
    dec = declared_model.declared(conn)
    kinds = {k["name"]: k for k in dec["kinds"]}
    rels = {r["name"]: r for r in dec["relations"]}
    counts = {k["kind"]: k["count"] for k in obs["kinds"]}
    rel_counts: dict[str, int] = {}
    out_kinds = []
    for k in obs["kinds"]:
        d = kinds.get(k["kind"])
        out_kinds.append({**k, "declared": d is not None, **({f: d[f] for f in ("label", "description", "group", "colour",
                                                                                 "status")} if d else {})})
    out_rels = []
    for r in obs["relations"]:
        d = rels.get(r["rel"])
        rel_counts[r["rel"]] = rel_counts.get(r["rel"], 0) + r["n"]
        allowed = bool(d) and (not d["from_kinds"] or r["src_kind"] in d["from_kinds"]) and (
            not d["to_kinds"] or r["dst_kind"] in d["to_kinds"])
        out_rels.append({**plain(dict(r)), "declared": d is not None, "allowed": allowed,
                         **({"view": d["view"], "label": d["label"]} if d else {})})
    return {
        "kinds": out_kinds,
        "relations": out_rels,
        "groups": dec["groups"],
        "views": dec["views"],
        "model": {"kinds": [{**k, "count": counts.get(k["name"], 0)} for k in dec["kinds"]],
                  "relations": [{**r, "count": rel_counts.get(r["name"], 0)} for r in dec["relations"]]},
        "needs_definition": {
            "kinds": [k["kind"] for k in obs["kinds"] if k["kind"] not in kinds],
            "relations": sorted({r["rel"] for r in obs["relations"] if r["rel"] not in rels}),
            "not_allowed": [{"src_kind": r["src_kind"], "rel": r["rel"], "dst_kind": r["dst_kind"], "n": r["n"]}
                            for r in out_rels if r["declared"] and not r["allowed"]],
        },
    }


_KIND = r"(\bkind:\s*['\"]?){old}(?=['\"]?[\s,}}\]]|$)"


def rewrite_mapping(text: str, old: str, new: str) -> str:
    """A mapping's YAML with kind `old` renamed: `kind: old` (entities and link endpoints) and `old` in a
    `kinds: [...]` list (mentions). Comments and layout are kept."""
    text = re.sub(_KIND.format(old=re.escape(old)), lambda m: m.group(1) + new, text, flags=re.MULTILINE)

    def in_list(m: re.Match) -> str:
        return m.group(1) + re.sub(rf"(?<![\w-]){re.escape(old)}(?![\w-])", new, m.group(2)) + m.group(3)
    return re.sub(r"(\bkinds:\s*\[)([^\]]*)(\])", in_list, text)


def rename_kind(conn, old: str, new: str, by: str, reason: str | None = None) -> dict:
    """Rename a custom or draft kind everywhere at once: the ontology (and the link types that allow it), the
    register (entities, their keys and indexes, so Fuzer IDs stay) and the approved mappings that make it
    (each gets a new approved version). Core kinds are renamed in code."""
    k = declared_model.kind(conn, old)
    if not k:
        raise OntologyError(f"no kind {old!r}")
    if k["status"] == "core":
        raise OntologyError("core kinds are used by name in Fuzer's code; they're renamed in a release")
    declared_model._name(new)
    if declared_model.kind(conn, new):
        raise OntologyError(f"kind {new!r} already exists")
    conn.execute("update ontology_kind set name = %s, edited = true, updated_by = %s, updated_at = now() where name = %s",
                 (new, by, old))
    conn.execute("update ontology_relation set from_kinds = array_replace(from_kinds, %(o)s, %(n)s), "
                 "to_kinds = array_replace(to_kinds, %(o)s, %(n)s) where %(o)s = any(from_kinds) or %(o)s = any(to_kinds)",
                 {"o": old, "n": new})
    moved = {t: conn.execute(f"update {t} set kind = %s where kind = %s", (new, old)).rowcount
             for t in ("entity", "entity_key", "geo_index", "knowledge_index")}
    mappings = []
    for m in catalog.current_mappings(conn):
        text = rewrite_mapping(m["yaml"], old, new)
        if text != m["yaml"]:
            v = catalog.propose(conn, text, by, note=f"kind {old} renamed to {new}" + (f": {reason}" if reason else ""),
                                status="approved", parent_id=m["id"])
            mappings.append(f"{m['source']}/{m['record_type']} v{v['id']}")
    declared_model.audit(conn, by, "kind", new, "rename", {"name": old}, {"name": new, "entities": moved["entity"],
                                                                             "mappings": mappings}, reason)
    _refresh_summary(conn)
    conn.commit()
    return {"kind": new, "was": old, "entities": moved["entity"], "mappings": mappings}


def _refresh_summary(conn) -> None:
    """The cached observed model, recomputed without moving its build time (other checks compare against it)."""
    from psycopg.types.json import Jsonb

    conn.execute("update register_summary set value = %s where name = 'ontology'",
                 (Jsonb(plain(observed_model(conn, cached=False))),))
