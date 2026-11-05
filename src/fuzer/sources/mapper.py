"""The column mapper (#225): a mapping as a form, one row per column, and back to YAML.

Most curation is "this column means X": keep it as a field (with a name and a type), link the record to the
tag, well, equipment or place it names, or use it as the key. `form` reads a mapping into that; `to_yaml` writes
the form back over the original mapping, so anything the form doesn't show (hooks, mentions, measurements,
limits, links of other shapes, fields built from templates) is kept exactly as it was and listed as `kept`.

    form = {source, record_type, shape, dataset, key: [columns], kind, entity_key: {column, as} | None,
            columns: [{name, field: {name, as} | None, link: {rel, target, keep} | None}], kept: [what isn't shown]}
"""

import yaml

from ..core.jsonsafe import plain

# a column linked to ...: how the mapping says it
TARGETS = {
    "tag": {"kind": "tag", "as": "tag"},
    "well": {"kind": "well", "as": "well"},
    "equipment": {"kind": "equipment", "as": "serial"},
    "tag_or_well": {"as": "tag_or_well"},
    "facility_or_well": {"as": "facility_or_well"},
    "floc": {"as": "floc"},
}
TARGET_LABELS = {"tag": "a tag", "well": "a well", "equipment": "equipment (by serial number)",
                 "tag_or_well": "a tag or a well", "facility_or_well": "a facility or a well",
                 "floc": "a functional location (FLOC)"}
FIELD_AS = {None: "text", "number": "number", "upper": "upper case"}
KEY_AS = {None: "as written", "tag": "a tag", "well": "a well", "serial": "a serial number", "upper": "upper case"}
LINK_KEYS = {"rel", "to", "when", "confidence", "valid_from", "valid_to"}


class MapperError(ValueError):
    pass


class _Dumper(yaml.SafeDumper):
    """Lists indented under their key (links:\n  - rel: ...), as the mapping files are written, so a one-line change
    is a one-line diff."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _target(to) -> str | None:
    if not isinstance(to, dict) or set(to) - {"kind", "from", "as"} or not isinstance(to.get("from"), str):
        return None
    for name, spec in TARGETS.items():
        if all(to.get(k) == v for k, v in spec.items()) and set(to) - {"from"} == set(spec):
            return name
    return None


def form(text: str, columns: list[str] | None = None) -> dict:
    """A mapping as the column form. `columns` (the table's, in order) adds the ones the mapping doesn't use yet."""
    try:
        raw = yaml.safe_load(text or "") or {}
    except yaml.YAMLError as e:
        raise MapperError(f"not YAML: {e}") from e
    if not isinstance(raw, dict):
        raise MapperError("a mapping is a YAML mapping")
    cols: dict[str, dict] = {c: {"name": c, "field": None, "link": None} for c in columns or []}

    def col(name: str) -> dict:
        return cols.setdefault(name, {"name": name, "field": None, "link": None})

    kept = [k for k in raw if k not in ("source", "record_type", "shape", "dataset", "key", "entity", "links",
                                        "location", "connector", "watermark", "catalog")]
    ent = raw.get("entity")
    kind, entity_key = None, None
    if isinstance(ent, dict):
        if "node" in ent:
            kept.append("entity (a resolver picks its kind)")
        else:
            kind = ent.get("kind")
            k = ent.get("key", "_key")
            if isinstance(k, str) and k != "_key":
                entity_key = {"column": k, "as": None}
            elif isinstance(k, dict) and set(k) <= {"from", "as"} and isinstance(k.get("from"), str):
                entity_key = {"column": k["from"], "as": k.get("as")}
            elif k != "_key":
                kept.append("the entity key (a template)")
            for extra in set(ent) - {"kind", "key", "fields"}:
                kept.append(f"entity {extra}")
            fields = ent.get("fields") or []
            for f in fields if isinstance(fields, list) else [[k_, v] for k_, v in fields.items()]:
                if isinstance(f, str):
                    col(f)["field"] = {"name": f, "as": None}
                elif isinstance(f, list) and len(f) == 2 and isinstance(f[1], str):
                    col(f[1])["field"] = {"name": f[0], "as": None}
                elif (isinstance(f, list) and len(f) == 2 and isinstance(f[1], dict) and set(f[1]) <= {"from", "as"}
                      and isinstance(f[1].get("from"), str) and f[1].get("as") in FIELD_AS):
                    col(f[1]["from"])["field"] = {"name": f[0], "as": f[1].get("as")}
                else:
                    kept.append(f"field {f[0] if isinstance(f, list) else f}")
    for link in raw.get("links") or []:
        t = _target(link.get("to")) if isinstance(link, dict) and not set(link) - LINK_KEYS and "from" not in link else None
        c = cols.get(link["to"]["from"]) if t else None
        if t and (c is None or c["link"] is None):
            col(link["to"]["from"])["link"] = {"rel": link["rel"], "target": t,
                                                "keep": {k: v for k, v in link.items() if k not in ("rel", "to")}}
        else:
            kept.append(f"link {link.get('rel') if isinstance(link, dict) else link}")
    key = raw.get("key")
    return plain({"source": raw.get("source"), "record_type": raw.get("record_type"), "shape": raw.get("shape", "records"),
                  "dataset": raw.get("dataset"), "key": key if isinstance(key, list) else ([key] if key else []),
                  "kind": kind, "entity_key": entity_key, "columns": list(cols.values()), "kept": kept,
                  "choices": {"targets": TARGET_LABELS, "field_as": {k or "": v for k, v in FIELD_AS.items()},
                              "key_as": {k or "": v for k, v in KEY_AS.items()}}})


def to_yaml(f: dict, base: str) -> str:
    """The form written over the original mapping: what the form shows is replaced, everything else is kept."""
    from .catalog import _parse

    raw = yaml.safe_load(base or "") or {}
    old_ent = raw.get("entity") if isinstance(raw.get("entity"), dict) else {}
    out = {**raw}
    for k in ("source", "record_type", "shape", "dataset"):
        if f.get(k):
            out[k] = f[k]
    keys = [k for k in f.get("key") or [] if k]
    if keys:
        out["key"] = keys[0] if len(keys) == 1 else keys
    managed_fields, managed_links = [], []
    for c in f.get("columns") or []:
        if c.get("field") and c["field"].get("name"):
            a = c["field"].get("as") or None
            name = c["field"]["name"]
            managed_fields.append(name if name == c["name"] and not a else [name, {"from": c["name"], "as": a} if a else c["name"]])
        if c.get("link") and c["link"].get("target") in TARGETS:
            spec = {**TARGETS[c["link"]["target"]], "from": c["name"]}
            to = {k: spec[k] for k in ("kind", "from", "as") if k in spec}
            managed_links.append({"rel": c["link"].get("rel") or "concerns", "to": to, **(c["link"].get("keep") or {})})
    # keep what the form doesn't show: fields and links it couldn't read
    shown = form(base)
    kept_fields = [x for x in (old_ent.get("fields") or []) if not _shown_field(x, shown)] \
        if isinstance(old_ent.get("fields"), list) else []
    kept_links = [x for x in raw.get("links") or [] if not _shown_link(x, shown)]
    if f.get("kind") and "node" not in old_ent:
        ent = {k: v for k, v in old_ent.items() if k not in ("kind", "key", "fields")}
        ek = f.get("entity_key") or {}
        key_spec = ({"from": ek["column"], "as": ek["as"]} if ek.get("as") else ek["column"]) if ek.get("column") else None
        out["entity"] = {"kind": f["kind"], **({"key": key_spec} if key_spec else {}), **ent,
                         "fields": managed_fields + kept_fields}
    elif "node" in old_ent:
        out["entity"] = {**old_ent, "fields": managed_fields + kept_fields}
    links = managed_links + kept_links
    if links:
        out["links"] = links
    else:
        out.pop("links", None)
    head = ""
    for line in (base or "").splitlines():  # the comment block at the top says what the mapping is: keep it
        if not line.startswith("#"):
            break
        head += line + "\n"
    text = head + yaml.dump(plain(out), Dumper=_Dumper, sort_keys=False, allow_unicode=True, default_flow_style=None,
                            width=110)
    _parse(text)  # a form that makes an invalid mapping says so here
    return text


def _shown_field(x, shown: dict) -> bool:
    names = {c["field"]["name"] for c in shown["columns"] if c["field"]}
    return (x if isinstance(x, str) else x[0] if isinstance(x, list) else None) in names


def _shown_link(x, shown: dict) -> bool:
    return any(c["link"] and isinstance(x, dict) and x.get("rel") == c["link"]["rel"] and isinstance(x.get("to"), dict)
               and x["to"].get("from") == c["name"] for c in shown["columns"])
