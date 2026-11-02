"""Mappings: what each column of a dataset means, as YAML, read by one generic extractor.

One file per source record type (src/fuzer/mappings/, plus any folders in $FUZER_MAPPINGS, plus
mappings approved in the review queue and stored in mapping_version; later ones override earlier ones
for the same source and record type):

    source: sap
    record_type: equipment
    shape: records          # records | hierarchy | network | timeseries | events | documents | reference |
                            # geospatial | measurements | curves | limits
    dataset: sap_equipment  # tables only: the name it has in the data folder
    key: equnr              # tables only: record key column, or a list joined by ">"
    watermark: timestamp    # optional: append-only lake tables read rows at or after the last value
    entity:                 # what the row is; omit for rows that are only links (BOM lines, network edges)
      kind: equipment
      key: {from: serial, as: serial}
      block: {from: floc, as: tag}
      fields: [description, manufacturer, model, serial]
    links:
      - rel: installed_at
        to: {kind: tag, from: floc, as: tag}
        valid_from: valid_from
    mentions:               # tags, wells and aliases found in free text
      - from: text
        props: [text, span, method]
    hooks: [name]           # named Python for logic that truly is source-specific (extract.HOOKS)
    curves:                 # reference curves landed in equipment_curve (OEM pump curves, choke characteristics)
      of: {kind: equipment_model, template: "{manufacturer}|{model}", as: model_key}
      curve: {from: curve}  # pump | choke; series, x, y, x_unit, y_unit likewise (text or a value)
      series: {from: series}
      x: x
      y: y
    measurements:           # readings landed in the measurement table (shape: measurements)
      of: {kind: well, from: well, as: well}   # what was measured; a list is tried in order; self
      at: test_date
      method: well test     # quantity, unit and method: text as written, or a value ({from: column})
      location: cml         # optional column: where on the thing
      values:
        - {quantity: oil_rate, from: oil_sm3d, unit: Sm3/d}
        - {quantity: {from: analyte}, from: result, unit: {from: unit}}
    limits:                 # operating limits per PI point, landed in point_limit (alarms, IOWs)
      each: attributes      # optional: a list in the record, one item per point (AF attributes)
      of: {kind: timeseries, from: pi_point, as: upper}   # a list is tried in order; a tag with one point does
      basis: alarm          # alarm | iow; basis, unit, level and note: text or a value
      unit: {from: uom}
      level: {from: priority}
      allowed: max_time_outside   # optional column: time allowed outside ("2 h"), stored in minutes
      enabled: enabled      # optional column: N / no / false / 0 is disabled
      valid_from: effective_date
      valid_to: superseded_date
      types: {PVHH: hihi, PVHI: hi}   # optional: the source's names for the limit types
      values:
        - {type: hihi, from: HiHi}
        - {type: {from: alarm}, from: setpoint}

A `ref` endpoint is a template ("sap:equipment:{equnr}") or a prefix with `from`
({ref: "sap:equipment", from: equnr}). Values: a column name, `_key` (the record key), `_row` (the whole row), or
{from, as, default, template, area_from, keep}; a default can itself be a value. `as` is a value transform (tag, well, serial, upper,
number) or a resolver that returns both kind and key (floc, af_path, af_parent, ec_node,
tag_or_well, facility_or_well; extract.RESOLVERS). Fields listed by name, or given as values, are
dropped when empty unless `keep: true`. A link is skipped when `when` names an empty column (or, as
{column: value}, a column without that value), when `unless: {column: value}` matches, or when a
resolver finds nothing. Order of work: entity and fields, links, mentions, hooks. Measurements are
read separately (measure), after the graph is built, and their values converted to the units in
units.yaml.
"""

import os
from functools import lru_cache
from pathlib import Path

import yaml

from . import extract as ex
from . import nel, units
from .model import Candidate, CurvePoint, Edge, Limit, Measurement

DEFAULT_DIR = Path(__file__).parent / "mappings"
SHAPES = {"records", "hierarchy", "network", "timeseries", "events", "documents", "reference", "geospatial",
          "measurements", "curves", "limits"}


class MappingError(ValueError):
    pass


def load(conn=None) -> dict[tuple[str, str], "Mapping"]:
    """Every mapping by (source, record_type): the built-in files, then $FUZER_MAPPINGS folders, then
    (with a connection) the latest approved version of each mapping stored in mapping_version."""
    extra = os.environ.get("FUZER_MAPPINGS", "")
    files = _load(extra)
    if conn is None or not conn.execute("select to_regclass('mapping_version') as t").fetchone()["t"]:
        return files
    ver = conn.execute("select coalesce(max(id), 0) as v from mapping_version").fetchone()["v"]
    if not ver:
        return files
    if (extra, ver) not in _APPROVED:
        merged = dict(files)
        for r in conn.execute("select distinct on (source, record_type) yaml, id from mapping_version "
                              "order by source, record_type, id desc"):
            m = Mapping(yaml.safe_load(r["yaml"]))
            merged[(m.source, m.record_type)] = m
        _APPROVED.clear()
        _APPROVED[(extra, ver)] = merged
    return _APPROVED[(extra, ver)]


_APPROVED: dict[tuple[str, int], dict] = {}


@lru_cache(maxsize=8)
def _load(extra: str) -> dict[tuple[str, str], "Mapping"]:
    out = {}
    for d in (DEFAULT_DIR, *(Path(p) for p in extra.split(os.pathsep) if p)):
        for f in sorted(d.glob("*.yaml")):
            m = Mapping(yaml.safe_load(f.read_text()), f)
            out[(m.source, m.record_type)] = m
    return out


def tables(conn=None) -> dict[str, "Mapping"]:
    """Mappings of tabular datasets by dataset name."""
    return {m.dataset: m for m in load(conn).values() if m.dataset}


SELF = object()  # endpoint: the record's own candidate
LIMIT_TYPES = ("hihi", "hi", "lo", "lolo")
DISABLED = {"n", "no", "false", "0", "disabled", "off"}


class Mapping:
    """One mapping file, checked and compiled once into small functions run per row."""

    def __init__(self, raw: dict, path: Path | None = None):
        self.raw, self.path = raw, path
        self._where = f" in {path.name}" if path else ""
        for k in ("source", "record_type", "shape"):
            if not raw.get(k):
                raise MappingError(f"missing {k!r}{self._where}")
        if raw["shape"] not in SHAPES:
            raise MappingError(f"unknown shape {raw['shape']!r}{self._where}; one of {sorted(SHAPES)}")
        self.source, self.record_type, self.shape = raw["source"], raw["record_type"], raw["shape"]
        self.dataset, self.watermark = raw.get("dataset"), raw.get("watermark")
        key = raw.get("key")
        self.key = tuple(key) if isinstance(key, list) else key
        if self.dataset and not self.key:
            raise MappingError(f"dataset {self.dataset!r} needs a key column{self._where}")

        self._entity = None
        if e := raw.get("entity"):
            if "node" in e:
                kind_key = self._endpoint(e["node"])
            else:
                kind, ckey = e.get("kind"), self._value(e.get("key", "_key"))
                if not kind:
                    raise MappingError(f"entity needs a kind or a node resolver{self._where}")
                kind_key = lambda k, r, p: (kind, ckey(k, r, p))  # noqa: E731
            spec = e.get("fields") or []
            fields = []
            for f in spec if isinstance(spec, list) else spec.items():
                name, expr = (f, f) if isinstance(f, str) else f
                fields.append((name, self._value(expr), isinstance(expr, dict) and bool(expr.get("keep"))))
            block = self._value(e["block"]) if e.get("block") else None
            self._entity = (kind_key, fields, block)
        self._links = [self._link(link) for link in raw.get("links") or []]
        self._mentions = [(m["from"], m.get("area_from"), set(m.get("kinds") or ()), m.get("method"),
                           m.get("rel", "mentions"), m.get("confidence"), m.get("props", ["text", "method"]))
                          for m in raw.get("mentions") or []]
        self.measures = self._measurements(raw["measurements"]) if raw.get("measurements") else None
        self.curve_spec = self._curves(raw["curves"]) if raw.get("curves") else None
        self.limit_spec = self._limits(raw["limits"]) if raw.get("limits") else None
        self._hooks = []
        for h in raw.get("hooks") or []:
            h = h if isinstance(h, dict) else {"name": h}
            if h["name"] not in ex.HOOKS:
                raise MappingError(f"unknown hook {h['name']!r}{self._where}")
            self._hooks.append((ex.HOOKS[h["name"]], {k: v for k, v in h.items() if k != "name"}))

    # ---- compile

    def _value(self, expr):
        """A value spec as f(key, row, pack)."""
        if isinstance(expr, str):
            if expr == "_key":
                return lambda k, r, p: k
            if expr == "_row":
                return lambda k, r, p: r
            return lambda k, r, p: r.get(expr)
        if "const" in expr:
            c = expr["const"]
            return lambda k, r, p: c
        if "template" in expr and not expr.get("as"):
            tmpl = expr["template"]
            f = lambda k, r, p: tmpl.format(_key=k, **r)  # noqa: E731
        else:
            if "template" in expr:  # a template, then a transform (e.g. "{manufacturer}|{model}" as model_key)
                tmpl = expr["template"]
                base = lambda k, r, p: tmpl.format(_key=k, **r)  # noqa: E731
            else:
                base = self._value(expr.get("from", "_key"))
            t = expr.get("as")
            if t is None:
                f = base
            elif t in ex.RESOLVERS:
                res = ex.RESOLVERS[t]

                def f(k, r, p, base=base, res=res):
                    found = res(base(k, r, p), p)
                    return found[1] if found else None
            elif t in ex.TRANSFORMS:
                tr, area_col = ex.TRANSFORMS[t], expr.get("area_from")
                if area_col:
                    f = lambda k, r, p: tr(base(k, r, p), p, ex.area_in(r.get(area_col)))  # noqa: E731
                else:
                    f = lambda k, r, p: tr(base(k, r, p), p, None)  # noqa: E731
            else:
                raise MappingError(f"unknown transform {t!r}{self._where}")
        if "default" in expr:
            d = expr["default"]
            dflt = self._value(d) if isinstance(d, dict) else (lambda k, r, p: d)
            inner = f
            f = lambda k, r, p: v if (v := inner(k, r, p)) not in (None, "") else dflt(k, r, p)  # noqa: E731
        return f

    def _endpoint(self, spec):
        """An endpoint spec as f(key, row, pack) -> (kind, key) or None; SELF for the record itself."""
        if spec is None or spec == "self":
            return SELF
        if "ref" in spec:
            tmpl = spec["ref"]
            if "{" not in tmpl:  # {ref: "sap:equipment", from: equnr}
                val = self._value(spec.get("from", "_key"))
                return lambda k, r, p: ("ref", f"{tmpl}:{val(k, r, p)}")
            return lambda k, r, p: ("ref", tmpl.format(_key=k, **r))
        if (t := spec.get("as")) in ex.RESOLVERS:
            res, base = ex.RESOLVERS[t], self._value(spec.get("from", "_key"))
            return lambda k, r, p: res(base(k, r, p), p)
        if not spec.get("kind"):
            raise MappingError(f"endpoint needs a kind, a ref or a resolver{self._where}")
        kind, val = spec["kind"], self._value({x: v for x, v in spec.items() if x != "kind"})
        return lambda k, r, p: (kind, val(k, r, p))

    def _link(self, link):
        if not link.get("rel"):
            raise MappingError(f"link without rel{self._where}")
        props = {name: self._value(v) for name, v in (link.get("props") or {}).items()} or None
        when, unless = link.get("when"), link.get("unless") or {}
        if not isinstance(unless, dict) or (when is not None and not isinstance(when, (str, dict))):
            raise MappingError(f"`when` is a column or {{column: value}}, `unless` is {{column: value}}{self._where}")

        def applies(row: dict) -> bool:
            if isinstance(when, str) and row.get(when) in (None, ""):
                return False
            if isinstance(when, dict) and any(row.get(k) != v for k, v in when.items()):
                return False
            return not (unless and all(row.get(k) == v for k, v in unless.items()))

        return (link["rel"], applies, link.get("each"), self._endpoint(link.get("from")),
                self._endpoint(link.get("to")), link.get("confidence", 1.0),
                self._value(link["valid_from"]) if link.get("valid_from") else None,
                self._value(link["valid_to"]) if link.get("valid_to") else None, props)

    def _measurements(self, spec):
        if not (spec.get("of") and spec.get("at") and spec.get("values")):
            raise MappingError(f"measurements need of, at and values{self._where}")
        text = lambda v: self._value(v) if isinstance(v, dict) else (lambda k, r, p: v)  # noqa: E731
        of = spec["of"] if isinstance(spec["of"], list) else [spec["of"]]
        if any(o == "self" for o in of) and not self._entity:
            raise MappingError(f"measurements of self need an entity{self._where}")
        values = []
        for v in spec["values"]:
            if not (v.get("quantity") and v.get("from")):
                raise MappingError(f"each measurement value needs a quantity and from{self._where}")
            values.append((text(v["quantity"]), self._value(v["from"]), text(v.get("unit"))))
        return ([self._endpoint(o) for o in of], self._value(spec["at"]), text(spec.get("method")),
                self._value(spec["location"]) if spec.get("location") else None, values)

    def _limits(self, spec):
        if not (spec.get("of") and spec.get("values")):
            raise MappingError(f"limits need of and values{self._where}")
        text = lambda v: self._value(v) if isinstance(v, dict) else (lambda k, r, p: v)  # noqa: E731
        col = lambda c: self._value(c) if c else (lambda k, r, p: None)  # noqa: E731
        of = spec["of"] if isinstance(spec["of"], list) else [spec["of"]]
        values = []
        for v in spec["values"]:
            if not (v.get("type") and v.get("from")):
                raise MappingError(f"each limit value needs a type and from{self._where}")
            values.append((text(v["type"]), self._value(v["from"])))
        return (spec.get("each"), [self._endpoint(o) for o in of], text(spec.get("basis", "alarm")), text(spec.get("unit")),
                text(spec.get("level")), col(spec.get("allowed")), col(spec.get("enabled")), col(spec.get("valid_from")),
                col(spec.get("valid_to")), text(spec.get("note")), {str(k).upper(): t for k, t in (spec.get("types") or {}).items()},
                values)

    def _curves(self, spec):
        if not (spec.get("of") and spec.get("series") and "x" in spec):
            raise MappingError(f"curves need of, series and x{self._where}")
        text = lambda v: self._value(v) if isinstance(v, dict) else (lambda k, r, p: v)  # noqa: E731
        return (self._endpoint(spec["of"]), text(spec.get("curve")), text(spec["series"]), self._value(spec["x"]),
                self._value(spec["y"]) if spec.get("y") else (lambda k, r, p: None),
                text(spec.get("x_unit", {"from": "x_unit"})), text(spec.get("y_unit", {"from": "y_unit"})))

    # ---- run

    def curve_points(self, key: str, row: dict, pack) -> list[CurvePoint]:
        """The curve point (or rating figure) in one row; rows without a number for x are skipped."""
        if not self.curve_spec:
            return []
        of_f, curve_f, series_f, x_f, y_f, xu_f, yu_f = self.curve_spec
        of = of_f(key, row, pack)
        x, y = units.reading(x_f(key, row, pack)), units.reading(y_f(key, row, pack))
        if not of or not of[1] or x is None:
            return []
        return [CurvePoint(f"{self.source}:{self.record_type}:{key}", of, str(curve_f(key, row, pack) or ""),
                           str(series_f(key, row, pack)), x[0], y[0] if y else None,
                           xu_f(key, row, pack) or None, yu_f(key, row, pack) or None)]

    def limits(self, key: str, row: dict, pack) -> list[Limit]:
        """The limits in one row (or in each item of its `each` list), in stored units; blanks and values
        that aren't numbers are skipped, as are types the mapping doesn't know."""
        if not self.limit_spec:
            return []
        each, of_f, basis_f, unit_f, level_f, allowed_f, enabled_f, from_f, to_f, note_f, names, values = self.limit_spec
        out = []
        items = enumerate(row.get(each) or [], 1) if each else [(None, row)]
        for i, item in items:
            record = f"{self.source}:{self.record_type}:{key}" + (f"#{i}" if i else "")
            of = [o for o in (o_f(key, item, pack) for o_f in of_f) if o and o[1]]
            if not of:
                continue
            unit, level, enabled = unit_f(key, item, pack) or None, level_f(key, item, pack), enabled_f(key, item, pack)
            for type_f, value_f in values:
                t = str(type_f(key, item, pack) or "")
                t = names.get(t.upper(), t.lower())
                read = units.reading(raw := value_f(key, item, pack))
                if read is None or t not in LIMIT_TYPES:
                    continue
                value, stored = units.normalise(read[0], unit)
                out.append(Limit(record, of, t, str(basis_f(key, item, pack) or "alarm").lower(), round(value, 6), stored,
                                 f"{raw} {unit}".strip() if stored != unit else None,
                                 str(level).strip().lower() if level not in (None, "") else None,
                                 units.minutes(allowed_f(key, item, pack)),
                                 str(enabled).strip().lower() not in DISABLED if enabled is not None else True,
                                 from_f(key, item, pack) or None, to_f(key, item, pack) or None,
                                 note_f(key, item, pack) or None))
        return out

    def measure(self, key: str, row: dict, pack) -> list[Measurement]:
        """The readings in one row, in stored units; values that aren't numbers are skipped."""
        if not self.measures:
            return []
        of_f, at_f, method_f, loc_f, values = self.measures
        at = at_f(key, row, pack)
        if not at:
            return []
        me = ("ref", f"{self.source}:{self.record_type}:{key}")
        of = [me if o is SELF else o(key, row, pack) for o in of_f]
        of = [o for o in of if o and o[1]]
        if not of:
            return []
        method, loc = method_f(key, row, pack), (loc_f(key, row, pack) or None) if loc_f else None
        out = []
        for quantity_f, value_f, unit_f in values:
            raw, quantity, unit = value_f(key, row, pack), quantity_f(key, row, pack), unit_f(key, row, pack)
            read = units.reading(raw)
            if read is None or not quantity:
                continue
            value, stored = units.normalise(read[0], unit)
            reported = f"{raw} {unit}".strip() if stored != unit else None
            out.append(Measurement(me[1], of, str(quantity), str(at), round(value, 6), stored, read[1], reported,
                                   method or None, str(loc) if loc else None))
        return out


    def extract(self, key: str, row: dict, pack) -> tuple[list[Candidate], list[Edge]]:
        c = None
        if self._entity:
            kind_key, fields, block = self._entity
            kind, ckey = kind_key(key, row, pack) or (None, None)
            values = {}
            for name, get, keep in fields:
                v = get(key, row, pack)
                if keep or v not in (None, ""):
                    values[name] = v
            c = Candidate(self.source, self.record_type, key, kind, ckey, values,
                          block=block(key, row, pack) if block else None)
        edges = c.edges if c else []
        me = ("ref", c.ref) if c else None
        for rel, applies, each, src_f, dst_f, conf, vf, vt, props in self._links:
            if not applies(row):
                continue
            for item in (row.get(each) or []) if each else (row,):
                src = me if src_f is SELF else src_f(key, item, pack)
                dst = me if dst_f is SELF else dst_f(key, item, pack)
                if src is None or dst is None:
                    continue
                edges.append(Edge(src, rel, dst, self.source, confidence=conf,
                                  valid_from=(vf(key, item, pack) or None) if vf else None,
                                  valid_to=(vt(key, item, pack) or None) if vt else None,
                                  props={n: f(key, item, pack) for n, f in props.items()} if props else None))
        for col, area_col, kinds, method, rel, conf, keep in self._mentions:
            area = ex.area_in(row.get(area_col)) if area_col else None
            for m in nel.find_mentions(row.get(col) or "", pack, default_area=area):
                if kinds and m.kind not in kinds:
                    continue
                props = {"text": m.text, "span": [m.start, m.end], "method": method or m.method}
                edges.append(Edge(me, rel, (m.kind, m.key), self.source,
                                  confidence=m.confidence if conf is None else conf,
                                  props={x: props[x] for x in keep}))
        for fn, opts in self._hooks:
            fn(c, row, pack, **opts)
        return ([c] if c else []), ([] if c else edges)
