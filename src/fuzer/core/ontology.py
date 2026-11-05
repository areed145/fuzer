"""The data model as data (#241): entity kinds and link types, declared in the database and editable from the
Ontology page.

Fuzer ships its model in `ontology.yaml` (status `core`). `seed` puts it in the database; a core row a person
has edited is kept over a later change to the file. Kinds and link types a project adds are `custom`; a kind
made while writing a mapping, before anyone has described it, is a `draft`.

The register doesn't depend on this: a mapping may still produce a kind nobody declared. `check_mapping` says
so, and the Ontology page shows it as "needs definition" (`graph.queries.ontology` puts declared and
observed side by side). Renaming a kind, which also rewrites the register and its mappings, is
`graph.ontology.rename_kind`.
"""

import re
from functools import lru_cache
from pathlib import Path

import yaml
from psycopg.types.json import Jsonb

from .jsonsafe import plain

BUILTIN = Path(__file__).with_name("ontology.yaml")
SEED = "built-in"
NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
KIND_FIELDS = ("label", "description", "group", "colour", "label_field", "status")
RELATION_FIELDS = ("label", "description", "view", "dated", "from_kinds", "to_kinds", "status")
STATUSES = ("core", "custom", "draft")


class OntologyError(ValueError):
    pass


@lru_cache(maxsize=1)
def builtin() -> dict:
    """The shipped model: groups, views, kinds and relations (as rows)."""
    raw = yaml.safe_load(BUILTIN.read_text())
    kinds = {name: {"label": k["label"], "description": k.get("description", ""), "group": k.get("group"),
                    "colour": k.get("colour"), "label_field": k.get("label_field"), "status": "core"}
             for name, k in raw["kinds"].items()}
    relations = {name: {"label": r.get("label", name.replace("_", " ")), "description": r.get("description", ""),
                        "view": r.get("view"), "dated": bool(r.get("dated", False)),
                        "from_kinds": list(r.get("from") or []), "to_kinds": list(r.get("to") or []), "status": "core"}
                 for name, r in raw["relations"].items()}
    return {"groups": raw["groups"], "views": raw["views"], "kinds": kinds, "relations": relations}


# ---- reading -----------------------------------------------------------------------------------------

def kinds(conn) -> list[dict]:
    return [plain(dict(r)) for r in conn.execute("select * from ontology_kind order by name")]


def relations(conn) -> list[dict]:
    return [plain(dict(r)) for r in conn.execute("select * from ontology_relation order by name")]


def kind(conn, name: str) -> dict | None:
    r = conn.execute("select * from ontology_kind where name = %s", (name,)).fetchone()
    return plain(dict(r)) if r else None


def relation(conn, name: str) -> dict | None:
    r = conn.execute("select * from ontology_relation where name = %s", (name,)).fetchone()
    return plain(dict(r)) if r else None


def declared(conn) -> dict:
    """Everything the Ontology page and the mapping editor need to offer: groups, views, kinds, relations."""
    b = builtin()
    groups = list(b["groups"])
    ks = kinds(conn)
    groups += sorted({k["group"] for k in ks if k["group"] and k["group"] not in groups})
    views = dict(b["views"])
    rs = relations(conn)
    for r in rs:
        if r["view"] and r["view"] not in views:
            views[r["view"]] = r["view"].replace("_", " ").capitalize()
    return {"groups": groups, "views": [{"id": k, "label": v} for k, v in views.items()], "kinds": ks, "relations": rs}


def history(conn, obj: str | None = None, name: str | None = None, limit: int = 100) -> list[dict]:
    return [plain(dict(r)) for r in conn.execute(
        "select * from ontology_audit where (%(o)s::text is null or object = %(o)s) "
        "and (%(n)s::text is null or name = %(n)s) order by id desc limit %(l)s",
        {"o": obj, "n": name, "l": limit})]


# ---- changing (every change is audited) ----------------------------------------------------------------

def audit(conn, by: str, obj: str, name: str, action: str, old: dict | None = None, new: dict | None = None,
          reason: str | None = None) -> None:
    conn.execute("insert into ontology_audit (by, object, name, action, old, new, reason) values (%s, %s, %s, %s, %s, %s, %s)",
                 (by, obj, name, action, Jsonb(plain(old)) if old is not None else None,
                  Jsonb(plain(new)) if new is not None else None, reason))


def _name(name: str) -> str:
    if not NAME.match(name or ""):
        raise OntologyError(f"{name!r} isn't a name: lower case letters, digits and _, starting with a letter")
    return name


def _values(values: dict, fields: tuple[str, ...]) -> dict:
    unknown = set(values) - set(fields)
    if unknown:
        raise OntologyError(f"unknown field(s) {sorted(unknown)}; one of {list(fields)}")
    if "status" in values and values["status"] not in STATUSES:
        raise OntologyError(f"status is one of {list(STATUSES)}")
    if "colour" in values and values["colour"] and not re.match(r"^#[0-9a-fA-F]{6}$", values["colour"]):
        raise OntologyError("colour is #rrggbb")
    return values


def _kind_list(conn, names) -> list[str]:
    names = list(dict.fromkeys(names or []))
    known = {r["name"] for r in conn.execute("select name from ontology_kind where name = any(%s)", (names,))}
    if missing := [n for n in names if n not in known]:
        raise OntologyError(f"unknown kind(s) {missing}; add them first")
    return names


def add_kind(conn, name: str, by: str, reason: str | None = None, **values) -> dict:
    _name(name)
    values = _values({"status": "custom", **values}, KIND_FIELDS)
    if values["status"] == "core":
        raise OntologyError("core kinds come with Fuzer; add a custom or draft kind")
    if kind(conn, name):
        raise OntologyError(f"kind {name!r} already exists")
    row = {"label": name.replace("_", " ").capitalize(), "description": "", **values}
    conn.execute("insert into ontology_kind (name, label, description, \"group\", colour, label_field, status, updated_by) "
                 "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                 (name, row["label"], row["description"], row.get("group"), row.get("colour"), row.get("label_field"),
                  row["status"], by))
    audit(conn, by, "kind", name, "create", None, row, reason)
    conn.commit()
    return kind(conn, name)


def update_kind(conn, name: str, by: str, reason: str | None = None, **values) -> dict:
    old = kind(conn, name)
    if not old:
        raise OntologyError(f"no kind {name!r}")
    values = _values(values, KIND_FIELDS)
    if values.get("status") == "core" and old["status"] != "core":
        raise OntologyError("a kind can't be made core")
    if old["status"] == "core" and values.get("status", "core") != "core":
        raise OntologyError("a core kind stays core")
    new = {k: v for k, v in values.items() if old.get(k) != v}
    if not new:
        return old
    sets = ", ".join(f'"{k}" = %({k})s' for k in new)
    conn.execute(f"update ontology_kind set {sets}, edited = true, updated_by = %(by)s, updated_at = now() "
                 "where name = %(name)s", {**new, "by": by, "name": name})
    audit(conn, by, "kind", name, "update", {k: old[k] for k in new}, new, reason)
    conn.commit()
    return kind(conn, name)


def delete_kind(conn, name: str, by: str, reason: str | None = None) -> dict:
    old = kind(conn, name)
    if not old:
        raise OntologyError(f"no kind {name!r}")
    if old["status"] == "core":
        raise OntologyError("core kinds come with Fuzer and can't be deleted")
    n = conn.execute("select count(*) as n from entity where kind = %s and merged_into is null and retired_at is null",
                     (name,)).fetchone()["n"]
    if n:
        raise OntologyError(f"{n:,} entities are {name!r}; change the mappings that make them first")
    conn.execute("update ontology_relation set from_kinds = array_remove(from_kinds, %(k)s), "
                 "to_kinds = array_remove(to_kinds, %(k)s) where %(k)s = any(from_kinds) or %(k)s = any(to_kinds)", {"k": name})
    conn.execute("delete from ontology_kind where name = %s", (name,))
    audit(conn, by, "kind", name, "delete", old, None, reason)
    conn.commit()
    return old


def add_relation(conn, name: str, by: str, reason: str | None = None, **values) -> dict:
    _name(name)
    values = _values({"status": "custom", **values}, RELATION_FIELDS)
    if values["status"] == "core":
        raise OntologyError("core link types come with Fuzer; add a custom one")
    if relation(conn, name):
        raise OntologyError(f"link type {name!r} already exists")
    row = {"label": name.replace("_", " "), "description": "", "view": None, "dated": False, **values,
           "from_kinds": _kind_list(conn, values.get("from_kinds")), "to_kinds": _kind_list(conn, values.get("to_kinds"))}
    conn.execute("insert into ontology_relation (name, label, description, view, dated, from_kinds, to_kinds, status, updated_by) "
                 "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                 (name, row["label"], row["description"], row["view"], row["dated"], row["from_kinds"], row["to_kinds"],
                  row["status"], by))
    audit(conn, by, "relation", name, "create", None, row, reason)
    conn.commit()
    return relation(conn, name)


def update_relation(conn, name: str, by: str, reason: str | None = None, **values) -> dict:
    old = relation(conn, name)
    if not old:
        raise OntologyError(f"no link type {name!r}")
    values = _values(values, RELATION_FIELDS)
    if values.get("status", old["status"]) != old["status"] and "core" in (old["status"], values.get("status")):
        raise OntologyError("a link type can't be made core, and a core one stays core")
    for side in ("from_kinds", "to_kinds"):
        if side in values:
            values[side] = _kind_list(conn, values[side])
    new = {k: v for k, v in values.items() if old.get(k) != v}
    if not new:
        return old
    sets = ", ".join(f"{k} = %({k})s" for k in new)
    conn.execute(f"update ontology_relation set {sets}, edited = true, updated_by = %(by)s, updated_at = now() "
                 "where name = %(name)s", {**new, "by": by, "name": name})
    audit(conn, by, "relation", name, "update", {k: old[k] for k in new}, new, reason)
    conn.commit()
    return relation(conn, name)


def delete_relation(conn, name: str, by: str, reason: str | None = None) -> dict:
    old = relation(conn, name)
    if not old:
        raise OntologyError(f"no link type {name!r}")
    if old["status"] == "core":
        raise OntologyError("core link types come with Fuzer and can't be deleted")
    n = conn.execute("select count(*) as n from edge where rel = %s", (name,)).fetchone()["n"]
    if n:
        raise OntologyError(f"{n:,} links are {name!r}; change the mappings that make them first")
    conn.execute("delete from ontology_relation where name = %s", (name,))
    audit(conn, by, "relation", name, "delete", old, None, reason)
    conn.commit()
    return old


def seed(conn, by: str = SEED, commit: bool = True) -> dict:
    """Put the shipped model in the database: add what's missing, follow the file for core rows nobody has
    edited, and leave edited and custom rows alone. Returns what was added and updated. Schema init runs it
    inside its own transaction (commit=False)."""
    b = builtin()
    report = {"kinds_added": [], "kinds_updated": [], "relations_added": [], "relations_updated": []}
    have = {r["name"]: r for r in conn.execute("select * from ontology_kind")}
    for name, k in b["kinds"].items():
        cur = have.get(name)
        if cur is None:
            conn.execute("insert into ontology_kind (name, label, description, \"group\", colour, label_field, status, updated_by) "
                         "values (%s, %s, %s, %s, %s, %s, 'core', %s)",
                         (name, k["label"], k["description"], k["group"], k["colour"], k["label_field"], by))
            report["kinds_added"].append(name)
        elif cur["status"] == "core" and not cur["edited"] and any(cur[f] != k[f] for f in KIND_FIELDS):
            conn.execute("update ontology_kind set label = %s, description = %s, \"group\" = %s, colour = %s, label_field = %s, "
                         "updated_by = %s, updated_at = now() where name = %s",
                         (k["label"], k["description"], k["group"], k["colour"], k["label_field"], by, name))
            report["kinds_updated"].append(name)
    have = {r["name"]: r for r in conn.execute("select * from ontology_relation")}
    for name, r in b["relations"].items():
        cur = have.get(name)
        if cur is None:
            conn.execute("insert into ontology_relation (name, label, description, view, dated, from_kinds, to_kinds, status, updated_by) "
                         "values (%s, %s, %s, %s, %s, %s, %s, 'core', %s)",
                         (name, r["label"], r["description"], r["view"], r["dated"], r["from_kinds"], r["to_kinds"], by))
            report["relations_added"].append(name)
        elif cur["status"] == "core" and not cur["edited"] and any(cur[f] != r[f] for f in RELATION_FIELDS):
            conn.execute("update ontology_relation set label = %s, description = %s, view = %s, dated = %s, from_kinds = %s, "
                         "to_kinds = %s, updated_by = %s, updated_at = now() where name = %s",
                         (r["label"], r["description"], r["view"], r["dated"], r["from_kinds"], r["to_kinds"], by, name))
            report["relations_updated"].append(name)
    if commit:
        conn.commit()
    return {k: v for k, v in report.items() if v}


# ---- checking a mapping ----------------------------------------------------------------------------------

def check_mapping(conn, produces: list[tuple[str, str, str | None]]) -> list[str]:
    """Warnings for what a mapping would make that the ontology doesn't declare or allow.
    `produces` is (rel or "", from kind, to kind or None): ("", kind, None) for the entity it makes."""
    ks = {r["name"] for r in conn.execute("select name from ontology_kind")}
    rs = {r["name"]: r for r in conn.execute("select name, from_kinds, to_kinds from ontology_relation")}
    out: list[str] = []

    def say(msg: str):
        if msg not in out:
            out.append(msg)
    for rel, src, dst in produces:
        for k in (src, dst):
            if k and k not in ks:
                say(f"kind {k!r} isn't in the ontology: add it (Ontology > Add kind) so people and Claude know what it is")
        if not rel:
            continue
        r = rs.get(rel)
        if r is None:
            say(f"link type {rel!r} isn't in the ontology: add it, or use one that is")
            continue
        if rel == "refers_to":
            say("refers_to is a generic link: use a specific link type when one fits")
        if src and r["from_kinds"] and src not in r["from_kinds"]:
            say(f"{rel} links from {', '.join(r['from_kinds'])}, not {src}: allow {src} on the link type, or use another")
        if dst and r["to_kinds"] and dst not in r["to_kinds"]:
            say(f"{rel} links to {', '.join(r['to_kinds'])}, not {dst}: allow {dst} on the link type, or use another")
    return out
