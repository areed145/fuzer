"""Suggested mappings for tables Fuzer has no mapping for yet.

1. Profile: a sample of rows per column (types, how full, distinct values, samples) and how many
   values read as a tag, well, location, date, number, unit or known manufacturer.
2. Rules name each column's meaning from its header and values. Values that are keys of records
   Fuzer already holds (an equipment number in a work order) make it a reference to that record.
3. Claude (optional) names the columns rules could only call `code` or `other`, from the headers
   and samples; cached per table in llm_extraction like the other readers, never called by rebuilds.
4. A draft mapping (mappings.py syntax) goes to the review queue as a `mapping` item. Once accepted
   (as suggested or edited) it is approved as a new version in the catalog (mapping_version), and the next ingest
   loads the table.
"""

import json
import re
from collections import Counter
from pathlib import Path

from psycopg.types.json import Jsonb

from ..core import llm
from ..core.llm import call, text_hash, usage
from ..core.pack import Pack
from . import connectors
from .extract import RESOLVERS

SAMPLE_ROWS = 2000
PROMPT_VERSION = "2026-10-29.1"
CACHE = "schema"

MEANINGS = {
    "tag": "plant tag of a piece of equipment or an instrument",
    "well": "well name or code",
    "location": "functional location or plant hierarchy code",
    "parent": "parent of the row in the same hierarchy",
    "from": "start of a connection between two things",
    "to": "end of a connection between two things",
    "ref": "id of a record in another system (equipment number, notification number)",
    "timestamp": "date or date-time",
    "description": "short name or description",
    "text": "free text: comments, log entries, remarks",
    "manufacturer": "manufacturer or vendor",
    "model": "model or type designation",
    "serial": "serial number",
    "number": "measured, counted or money value",
    "unit": "unit of measure",
    "code": "code from a short list: type, status, priority, reason",
    "latitude": "latitude",
    "longitude": "longitude",
    "person": "person or role",
    "point": "historian point or time series name (a tag or well with a signal suffix, 20PT3003.PV)",
    "tag_or_well": "a tag on some rows and a well on others (a meter list naming plant meters and wellhead meters)",
    "other": "none of these, for example the row's own id",
}
REFERENCES = {"tag", "well", "tag_or_well", "location", "parent", "from", "to", "ref", "point"}

HEADER_HINTS = {
    "description": {"description", "descr", "desc", "descriptor", "shorttext", "name", "title", "service",
                    "equipmentdescription"},
    "manufacturer": {"manufacturer", "mfr", "mfg", "maker", "make", "vendor", "supplier", "oem"},
    "model": {"model", "modelno", "modelnumber", "type"},
    "serial": {"serial", "serialno", "serialnumber", "sn", "sno"},
    "unit": {"unit", "units", "uom", "engunits", "eu"},
    "latitude": {"lat", "latitude"},
    "longitude": {"lon", "lng", "long", "longitude"},
    "person": {"author", "user", "createdby", "operator", "testedby", "inspector", "person", "by", "owner",
               "responsible", "assignee", "assignedto", "planner"},
    "parent": {"parent", "superior", "parentlocation", "superiorloc", "parentfloc", "parenttag", "parentid",
               "parentactivity", "parentactivityid"},
    "code": {"wbs", "wbscode", "wbsid", "wbselement"},
    "from": {"from", "fromtag", "upstream", "fromnode", "fromequipment"},
    "to": {"to", "totag", "downstream", "tonode", "toequipment"},
    "text": {"comment", "comments", "remarks", "notes", "text", "log", "narrative"},
}
ID_HINT = re.compile(r"(id|no|nr|num|number|key|code|tag)$")
CODE_HINT = re.compile(r"(type|status|priority|class|category|code|grade|spec)$")
OBJECT_WORDS = ("object", "asset", "equip", "loc", "well", "tag", "node", "floc")
NUMBER_HINT = re.compile(r"(qty|quantity|stock|count|cost|rate|weight|volume|pressure|temp|level|flow|sm3|bbl|"
                         r"pct|percent|reading|value|amount|price|hours|duration|size)")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$")
# dates in an export's local format (31/12/2025, 12.31.2025 14:00, 06-Apr-2026): read as dates once the system's
# date format is set (formats.py; the Add a source page offers it)
LOCAL_DATE = re.compile(r"^(\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{1,2}[ -](jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                        r"[ -]\d{4})( \d{1,2}:\d{2}(:\d{2})?)?$", re.IGNORECASE)
POINT = re.compile(r"[./:][A-Z][A-Z0-9_]*$")  # a historian point's signal suffix: .PV, .WHP, /PV
START = {"start", "startdate", "plannedstart", "earlystart", "begin", "scheduledstart", "targetstart"}
FINISH = {"finish", "finishdate", "plannedfinish", "earlyfinish", "end", "enddate", "scheduledfinish", "targetfinish",
          "due", "duedate"}
# A measured column's unit, from the end of its header (volume_ksm3, energy_gj, hhv_mj_sm3): the quantity is the rest.
UNIT_SUFFIXES = [("mj_sm3", "MJ/Sm3"), ("ksm3d", "kSm3/d"), ("ksm3", "kSm3"), ("sm3d", "Sm3/d"), ("sm3", "Sm3"),
                 ("gj", "GJ"), ("barg", "barg"), ("kpa", "kPa"), ("psig", "psig"), ("degc", "degC"), ("degf", "degF"),
                 ("pct", "%"), ("ppm", "ppm"), ("mmscf", "MMscf"), ("mcf", "Mcf"), ("bbl", "bbl")]
INSTALLED = re.compile(r"(install|commission|startup|inservice|fitted)")
UNITS = {"barg", "bara", "bar", "psi", "psig", "kpa", "mpa", "degc", "degf", "%", "m3/h", "sm3/d", "bbl/d", "kg",
         "mm", "in", "mm/s", "hz", "rpm", "kw", "mw", "ppm", "m3", "l/min", "ft", "kgf/cm2"}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _snake(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.lower())).strip("_") or "column"


def _area(v: str, pack: Pack) -> str | None:
    return pack.tags.area_in(v)


def _num(v: str) -> float | None:
    try:
        return float(v.replace(",", ""))
    except ValueError:
        return None


# ---- profile


def known_manufacturers(conn, pack: Pack) -> set[str]:
    """Manufacturer names the pack and the register already know, upper-cased."""
    names = {str(v).upper() for v in (pack.classification.get("manufacturer_aliases") or {}).values()}
    names |= {str(k).upper() for k in (pack.classification.get("manufacturer_aliases") or {})}
    names |= {r["v"].upper() for r in conn.execute(
        "select distinct value #>> '{}' as v from golden where field = 'manufacturer' and value #>> '{}' <> ''")}
    return names


def profile(c, pack: Pack, sample_rows: int = SAMPLE_ROWS, makers: set[str] = frozenset()) -> dict:
    """Column statistics over the first `sample_rows` rows."""
    rows = []
    for row in c.rows():
        rows.append(row)
        if len(rows) >= sample_rows:
            break
    names = [name for name, _ in c.schema()] if c.schema() else list(rows[0] if rows else [])
    fac = pack.facility.upper()
    cols = []
    for name in names:
        vals = [str(r.get(name)).strip() for r in rows if r.get(name) not in (None, "")]
        n = len(vals) or 1
        counts = Counter(vals)
        frac = lambda test: sum(1 for v in vals if test(v)) / n  # noqa: B023
        nums = [x for v in vals if (x := _num(v)) is not None]
        cols.append({
            "name": name, "rows": len(rows), "filled": round(len(vals) / (len(rows) or 1), 3),
            "distinct": len(counts), "unique": len(counts) == len(vals) == len(rows) and len(rows) > 0,
            "samples": [v for v, _ in counts.most_common(5)] if len(counts) <= 20 else list(dict.fromkeys(vals))[:5],
            "avg_len": round(sum(map(len, vals)) / n, 1),
            "numeric": round(len(nums) / n, 3),
            "integer": bool(nums) and all(x == int(x) for x in nums),
            "max": max(nums) if nums else None,
            "date": round(frac(lambda v: bool(DATE.match(v) or LOCAL_DATE.match(v))), 3),
            "location": round(frac(lambda v: v.upper() == fac or v.upper().startswith(fac + "-")), 3),
            "tag": round(frac(lambda v: (pack.tags.parse(v) or pack.tags.parse(v, default_area=_area(v, pack))) is not None), 3),
            "tag_or_well": round(frac(lambda v: pack.tags.parse(v) is not None or pack.wells.parse(v) is not None), 3),
            "well": round(frac(lambda v: v.upper() == fac or pack.wells.parse(v) is not None), 3),
            "place": round(frac(lambda v: RESOLVERS["place"](v, pack) is not None), 3),
            "point": round(frac(lambda v: bool(POINT.search(v.upper())) and RESOLVERS["tag_or_well"](v, pack) is not None), 3),
            "spaces": round(frac(lambda v: " " in v), 3),
            "unit": round(frac(lambda v: v.lower() in UNITS), 3),
            "manufacturer": round(frac(lambda v: v.upper() in makers), 3),
        })
    return {"rows_sampled": len(rows), "columns": cols, "pairs": _unique_pairs(rows, cols)}


def _unique_pairs(rows: list[dict], cols: list[dict]) -> list[list[str]]:
    """Pairs of full columns, neither unique alone, that are unique together (a meter and its day): a table with no
    id column of its own is keyed on them."""
    if not rows or any(c["unique"] and c["filled"] == 1 for c in cols):
        return []
    full = [c["name"] for c in cols if c["filled"] == 1][:30]
    return [[a, b] for i, a in enumerate(full) for b in full[i + 1:]
            if len({(str(r.get(a)).strip(), str(r.get(b)).strip()) for r in rows}) == len(rows)]


def references(conn, prof: dict, rows_by_col: dict[str, list[str]], exclude: set[tuple[str, str]],
               key: str | None = None) -> dict:
    """Columns whose values are mostly keys of one kind of record Fuzer already holds. Number ranges
    overlap between record types, so a target whose key column has this column's name wins."""
    from . import mappings

    kinds = [(r["source"], r["record_type"]) for r in conn.execute(
        "select distinct source, record_type from source_record")]
    key_cols = {(m.source, m.record_type): m.key for m in mappings.load(conn).values() if isinstance(m.key, str)}
    out = {}
    for col in prof["columns"]:
        vals = rows_by_col.get(col["name"]) or []
        if not vals or col["name"] == key:
            continue
        sample = list(dict.fromkeys(vals))[:200]
        best = None
        for source, rtype in kinds:
            if (source, rtype) in exclude:
                continue
            hit = conn.execute("select count(*) as n from source_record where source = %s and record_type = %s "
                               "and source_key = any(%s)", (source, rtype, sample)).fetchone()["n"]
            rank = (_norm(key_cols.get((source, rtype)) or "") == _norm(col["name"]), hit)
            if hit / len(sample) >= 0.8 and (best is None or rank > best[2]):
                best = (source, rtype, rank)
        if best:
            out[col["name"]] = f"{best[0]}:{best[1]}"
    return out


# ---- rules


def pick_key(prof: dict) -> str | list[str] | None:
    """The row's own id: a full, unique column, preferring one whose header says id / number / code. With none, a
    thing and a date that are unique together (meter, gas_day)."""
    keyable = [c["name"] for c in prof["columns"] if c["unique"] and c["filled"] == 1]
    found = next((c for c in keyable if ID_HINT.search(_norm(c))), None) or (keyable[0] if keyable else None)
    if found:
        return found
    # an export with a few repeated ids still has an id column: the load holds it for the duplicates (#225)
    nearly = [c for c in prof["columns"] if c["filled"] == 1 and c["rows"] and c["distinct"] >= 0.95 * c["rows"]
              and ID_HINT.search(_norm(c["name"]))]
    if nearly:
        return max(nearly, key=lambda c: c["distinct"])["name"]
    col = {c["name"]: c for c in prof["columns"]}
    asset = lambda c: max(col[c]["tag_or_well"], col[c]["location"]) >= 0.8
    found = []
    for a, b in prof.get("pairs", []):
        dated = [x for x in (a, b) if col[x]["date"] >= 0.9]
        other = [x for x in (a, b) if x not in dated]
        if len(dated) == 1 and other and (asset(other[0]) or ID_HINT.search(_norm(other[0]))):
            found.append((not asset(other[0]), [other[0], dated[0]]))  # the thing first: A1FT1002 on 2026-11-08
    return min(found)[1] if found else None  # a plant tag or well over the source's own id for it


def rules(name: str, prof: dict, refs: dict | None = None, values: dict | None = None) -> dict:
    """Each column's meaning (with why), the key column, shape and entity kind. `values` (sampled
    values per column) lets a parent column be found by its values, not only its header."""
    refs = refs or {}
    rows = prof["rows_sampled"]
    key = pick_key(prof)
    one = key if isinstance(key, str) else None  # the row's own id column; a pair keys the row but names neither
    out = {}
    for c in prof["columns"]:
        n = _norm(c["name"])
        hint = next((m for m, names in HEADER_HINTS.items() if n in names), None)
        refish = c["date"] < 0.5 and c["numeric"] < 0.5
        names_one = c["spaces"] < 0.5  # a reference column holds names, not sentences that mention them
        code_hint = bool(CODE_HINT.search(n)) and not any(w in n for w in OBJECT_WORDS) and c["distinct"] <= 50
        if hint in ("description", "manufacturer", "model", "serial", "unit", "latitude", "longitude", "person"):
            m, why = hint, "header"
        elif hint == "code" and c["name"] != key:
            m, why = "code", "header names a code"
        elif hint in ("parent", "from", "to") and refish:
            m, why = hint, "header"
        elif c["date"] >= 0.9:
            m, why = "timestamp", "values are dates"
        elif c["location"] >= 0.8 and names_one:
            m, why = "location", "values are plant locations"
        elif code_hint and c["name"] != key:
            m, why = "code", "header names a code"
        elif c.get("point", 0) >= 0.8 and names_one:
            m, why = "point", "values are historian point names (a tag or well with a signal suffix)"
        elif c["tag"] >= 0.8 and c["tag"] >= c["well"] and names_one:
            m, why = "tag", "values read as tags"
        elif c["well"] >= 0.8 and names_one:
            m, why = "well", "values read as wells"
        elif c["tag_or_well"] >= 0.8 and names_one and min(c["tag"], c["well"]) >= 0.1:
            m, why = "tag_or_well", "values read as tags on some rows and wells on others"
        elif c["tag_or_well"] >= 0.8 and names_one:
            m, why = ("tag", "values read as tags or wells") if c["tag"] >= c["well"] else ("well", "values read as wells or tags")
        elif c.get("place", 0) >= 0.6 and names_one and c["name"] != key:
            m, why = "location", "values are plant locations (tags, wells, areas) written in several styles"
        elif c["name"] in refs and c["name"] != key:
            m, why = "ref", f"values are keys of {refs[c['name']]} records"
        elif c["numeric"] >= 0.95:
            if c["integer"] and (c["name"] == key or c["unique"] and ID_HINT.search(n)):
                m, why = "other", "unique whole numbers (an id)"
            elif NUMBER_HINT.search(n):
                m, why = "number", "numeric, header names a quantity"
            elif c["integer"] and c["distinct"] <= 12 and c["max"] <= 20:
                m, why = "code", "a few small whole numbers"
            else:
                m, why = "number", "numeric"
        elif c["unit"] >= 0.8:
            m, why = "unit", "values are units"
        elif c["manufacturer"] >= 0.6:
            m, why = "manufacturer", "values are known manufacturers"
        elif c["avg_len"] >= 12 and c["spaces"] >= 0.6:
            m, why = ("text", "header") if hint == "text" else (
                ("text", "long free text") if c["avg_len"] >= 60 else ("description", "short phrases"))
        elif hint == "text":
            m, why = "text", "header"
        elif not c["unique"] and c["distinct"] <= max(20, rows // 20):
            m, why = "code", "few distinct values"
        else:
            m, why = "other", "no rule matched"
        out[c["name"]] = {"meaning": m, "by": "rules", "why": why}

    if one and values and out[one]["meaning"] in ("location", "tag", "well", "other"):
        # A column holding values of the key column, in the same domain, is the parent (a hierarchy).
        keyset = set(values.get(one, []))
        for c in prof["columns"]:
            vals = values.get(c["name"]) or []
            if c["name"] != one and vals and out[c["name"]]["meaning"] in (out[one]["meaning"], "code", "other") \
                    and sum(v in keyset for v in vals) / len(vals) >= 0.8:
                out[c["name"]] = {"meaning": "parent", "by": "rules", "why": "values are keys of this table"}
        parents = [c for c, v in out.items() if v["meaning"] == "parent"]
        if len(parents) > 1:  # one parent: the column whose header says so, else the most specific (most distinct)
            named = [c for c in parents if "parent" in _norm(c) or HEADER_HINTS["parent"] & {_norm(c)}]
            distinct = {c["name"]: c["distinct"] for c in prof["columns"]}
            keep = named[0] if named else max(parents, key=lambda c: distinct.get(c, 0))
            for c in parents:
                if c != keep:
                    out[c] = {"meaning": "code", "by": "rules", "why": f"also keys of this table; {keep} is the parent"}
    return {"columns": out, "key": key, **_shape(out, key)}


def _shape(cols: dict, key) -> dict:
    meanings = {c: v["meaning"] for c, v in cols.items()}
    has = lambda m: m in meanings.values()
    key_m = meanings.get(key) if isinstance(key, str) else None
    if isinstance(key, list) and has("number") and not has("text") and \
            {meanings.get(k) for k in key} & {"tag", "well", "tag_or_well", "location"} and \
            "timestamp" in {meanings.get(k) for k in key}:
        # readings of one thing at one time (a meter's daily volume, a tank's gauge): measurements on that thing
        return {"shape": "measurements", "kind": None}
    if has("parent"):
        shape = "hierarchy"
    elif has("from") and has("to"):
        shape = "network"
    elif has("timestamp") and (has("text") or has("description")) and not has("serial") and \
            key_m not in ("tag", "well", "location"):
        shape = "events"
    else:
        shape = "records"
    kind = {"tag": "tag", "well": "well", "location": "location"}.get(key_m)
    if key_m == "point":
        shape, kind = "timeseries", "timeseries"
    elif has("serial") and (has("manufacturer") or has("model")):
        # an equipment list (#411): the physical items, keyed on serial and blocked by the tag they're installed
        # at, so a contractor's copy matches SAP's equipment at once
        kind = "equipment"
    elif kind is None and has("serial"):
        kind = "equipment"
    elif key_m not in ("tag", "well", "location") and _dated(cols, START) and _dated(cols, FINISH):
        kind = "activity"  # planned start and finish: a schedule's projects and activities (the shipped kind, #241)
        shape = "hierarchy" if shape == "hierarchy" else "records"
    return {"shape": shape, "kind": kind}


def _dated(cols: dict, names: set[str]) -> str | None:
    return next((c for c, v in cols.items() if v["meaning"] == "timestamp" and _norm(c) in names), None)


# ---- draft mapping


# Link types for what a suggested entity points at, where the ontology has a specific one (#241); refers_to otherwise.
RELS = {("activity", "location"): "located_at", ("activity", "tag"): "located_at", ("activity", "well"): "located_at",
        ("activity", "work_order"): "executes", ("equipment", "tag"): "installed_at"}


def draft(dataset: str, sug: dict, source: str | None = None, record_type: str | None = None) -> dict:
    """A mapping (mappings.py syntax) from a suggestion, for a reviewer to accept or edit. `source` and
    `record_type` default to the dataset name's parts (sap_equipment: sap, equipment)."""
    first, _, rest = dataset.partition("_")
    source, record_type = source or first, record_type or rest or "row"
    cols, key, kind = sug["columns"], sug["key"], sug["kind"]
    m = {"source": source, "record_type": record_type, "shape": sug["shape"], "dataset": dataset}
    if key:
        m["key"] = key
    if sug["shape"] == "measurements":
        return {**m, "measurements": _measured(cols, key)}
    one = key if isinstance(key, str) else None
    as_of = {"tag": {"as": "tag"}, "well": {"as": "well"}, "location": {"as": "floc"}}
    by = lambda meaning: [c for c, v in cols.items() if v["meaning"] == meaning]
    entity = None
    if kind == "timeseries":
        entity = {"kind": "timeseries", "key": {"from": key, "as": "upper"}}
    elif kind in ("tag", "well"):
        entity = {"kind": kind, "key": {"from": key, "as": kind}}
    elif kind == "location":
        entity = {"node": {"from": key, "as": "floc"}}
    elif kind == "equipment":
        entity = {"kind": "equipment", "key": {"from": by("serial")[0], "as": "serial"}}
        if tags := by("tag") or by("location"):
            entity["block"] = {"from": tags[0], "as": "tag"}
    elif sug["shape"] != "network":
        entity = {"kind": kind or record_type, **({"key": one} if one else {})}
    if entity is not None:
        fields = []
        for c, v in cols.items():
            mean = v["meaning"]
            if mean in REFERENCES or c == key and mean == "other":
                continue
            name = mean if mean in ("description", "manufacturer", "model", "serial") else _snake(c)
            if kind == "timeseries" and mean == "unit":
                name = "uom"  # what the time series providers read the unit from
            if mean == "number":
                fields.append([name, {"from": c, "as": "number"}])
            elif mean == "timestamp":
                fields.append([name, {"from": c, "as": "date"}])  # a detected date stays a date (#411)
            else:
                fields.append(c if name == c else [name, c])
        entity["fields"] = fields
        m["entity"] = entity
    own = entity.get("kind") if entity else None

    def endpoint(c):
        mean = cols[c]["meaning"]
        if mean == "ref" or cols[c].get("ref"):
            return {"ref": cols[c].get("ref") or "unknown:record", "from": c}
        if mean == "point":
            return {"kind": "timeseries", "from": c, "as": "upper"}
        if mean == "tag_or_well":
            return {"from": c, "as": "tag_or_well"}
        if mean == "location":
            return {"from": c, "as": "place"}  # tags, wells, areas and the facility, in whatever style it's written
        if mean == "parent" and own and own not in ("tag", "well") and (not one or cols[one]["meaning"] == "other"):
            return {"kind": own, "from": c}  # the parent is another row of this table, by its key as written
        domain = mean if mean in as_of else (cols[one]["meaning"] if one and cols[one]["meaning"] in as_of else "tag")
        if domain == "location":
            return {"from": c, "as": "floc"}
        return {"kind": domain, "from": c, **as_of[domain]}

    def rel(c, default):
        target = (cols[c].get("ref") or "").partition(":")[2] or cols[c]["meaning"]
        return RELS.get((own, target), default)

    links = []
    for c in by("parent"):
        links.append({"rel": "part_of", "when": c, "to": endpoint(c)})
    if (f := by("from")) and (t := by("to")):
        link = {"rel": "connects_to", "from": endpoint(f[0]), "to": endpoint(t[0])}
        if entity is None:  # a network table: its other columns describe the connection
            props = {_snake(c): ({"from": c, "as": "number"} if v["meaning"] == "number" else c)
                     for c, v in cols.items() if v["meaning"] not in REFERENCES}
            if props:
                link["props"] = props
        links.append(link)
    if kind == "timeseries":  # a point list: each point belongs to the tag or well its name contains
        links.append({"rel": "has_timeseries", "from": {"from": "_key", "as": "tag_or_well"}, "to": "self",
                      "confidence": 0.9})
    for c, v in cols.items():
        if v["meaning"] not in ("tag", "well", "tag_or_well", "location", "ref", "point"):
            continue
        if kind == "equipment" and entity and entity.get("block", {}).get("from") == c:
            link = {"rel": "installed_at", "when": c, "to": {"kind": "tag", "from": c, "as": "tag"}}
            if since := next((x for x in by("timestamp") if INSTALLED.search(_norm(x))), None):
                link["valid_from"] = since  # installed there from this date
            links.append(link)
        elif c == key:
            continue
        elif entity is not None:
            links.append({"rel": rel(c, "refers_to"), "when": c, "to": endpoint(c)})
    if links:
        m["links"] = links
    if entity is not None and (texts := by("text")):
        m["mentions"] = [{"from": c} for c in texts]
    return m


def _measured(cols: dict, key: list[str]) -> dict:
    """What a measurements table measures: on the thing in its key, at the date in its key, each number column a
    quantity with the unit its header ends in."""
    thing = next(k for k in key if cols[k]["meaning"] != "timestamp")
    at = next(k for k in key if cols[k]["meaning"] == "timestamp")
    meaning = cols[thing]["meaning"]
    of = {"from": thing, "as": {"location": "place"}.get(meaning, meaning)}
    if meaning in ("tag", "well"):
        of = {"kind": meaning, **of}
    values = []
    for c, v in cols.items():
        if v["meaning"] != "number":
            continue
        name = _snake(c)
        quantity, unit = name, None
        for suffix, u in UNIT_SUFFIXES:
            if name.endswith("_" + suffix):
                quantity, unit = name[: -len(suffix) - 1], u
                break
        if unit is None and name.endswith("hours"):
            unit = "h"
        values.append({"quantity": quantity, "from": c, **({"unit": unit} if unit else {})})
    return {"of": of, "at": at, "values": values}


def document_draft(dataset: str, source: str, record_type: str = "document") -> dict:
    """The mapping for a folder of documents (connectors.DocumentFolder): row 0 of each file is the document, the
    rest its sections, part of it, searchable by their text and linked to the tags and wells the text mentions."""
    return {"source": source, "record_type": record_type, "shape": "documents", "dataset": dataset,
            "key": ["file", "section"],
            "entity": {"node": {"from": "_row", "as": "document_part"}, "fields": [
                "title", "heading", ["page", {"from": "page", "as": "number"}], "text", ["index", "section"],
                "format", ["pages", {"from": "pages", "as": "number"}], ["file_sha256", "sha256"], "needs_ocr",
                ["description", {"from": "heading", "default": {"from": "title"}, "keep": True}]]},
            "links": [{"rel": "part_of", "when": "heading", "to": {"kind": "document", "from": "file"}}],
            "mentions": [{"from": "text"}]}


def to_yaml(mapping: dict) -> str:
    """Block style at the top, short lists and maps inline, like the built-in mapping files."""
    import yaml

    return yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True, width=110, default_flow_style=None)


# ---- Claude


SYSTEM_PROMPT = """\
You name what the columns of a data table mean, for an asset data platform in oil and gas (plants,
wells, equipment, maintenance, production). You get the table name, every column with its type and
sample values, and what simple rules decided. Give a meaning, from the list, for each column you are
asked about; keep `other` for columns that really fit none. Also say which column, if any, is the
row's own unique id ("" if none)."""


def _claude_schema() -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["columns", "key"],
            "properties": {"key": {"type": "string"}, "columns": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["column", "meaning"],
                "properties": {"column": {"type": "string"}, "meaning": {"type": "string", "enum": list(MEANINGS)}}}}}}


def claude_input(dataset: str, prof: dict, sug: dict) -> dict:
    ask = [c for c, v in sug["columns"].items() if v["meaning"] in ("code", "other")]
    return {"table": dataset, "ask_about": ask, "columns": [
        {"column": c["name"], "type": "number" if c["numeric"] >= 0.95 else "date" if c["date"] >= 0.9 else "text",
         "filled": c["filled"], "distinct": c["distinct"], "samples": c["samples"],
         "rules": sug["columns"][c["name"]]["meaning"]} for c in prof["columns"]]}


def apply_claude(sug: dict, result: dict) -> dict:
    """Claude's meanings for the columns it was asked about; rules keep the rest."""
    cols = {c: dict(v) for c, v in sug["columns"].items()}
    for item in result.get("columns", []):
        c = item["column"]
        if c in cols and cols[c]["meaning"] in ("code", "other") and item["meaning"] != cols[c]["meaning"]:
            cols[c] = {"meaning": item["meaning"], "by": "claude", "why": "Claude, from header and samples"}
    key = sug["key"] or (result.get("key") if result.get("key") in cols else None)
    return {"columns": cols, "key": key, **_shape(cols, key)}


def cached_claude(conn, dataset: str, payload: dict) -> dict | None:
    row = conn.execute("select text_hash, prompt_version, result from llm_extraction where source = %s "
                       "and source_key = %s", (CACHE, dataset)).fetchone()
    h = text_hash(json.dumps(payload, sort_keys=True))
    return row["result"] if row and row["text_hash"] == h and row["prompt_version"] == PROMPT_VERSION else None


def ask_claude(conn, dataset: str, payload: dict, *, client=None, model: str | None = None,
               effort: str | None = None) -> tuple[dict, dict]:
    model, effort = model or llm.model(), effort or llm.effort()  # the settings (#131) unless given
    if (hit := cached_claude(conn, dataset, payload)) is not None:
        return hit, {"cached": True}
    if client is None:
        client = llm.client()
    legend = "Meanings: " + "; ".join(f"{k} = {v}" for k, v in MEANINGS.items())
    message = call(client, {
        "model": model, "max_tokens": 4000,
        "system": [{"type": "text", "text": f"{SYSTEM_PROMPT}\n\n{legend}", "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": _claude_schema()}}})
    if message.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"stopped: {message.stop_reason}")
    result = json.loads(next(b.text for b in message.content if b.type == "text"))
    conn.execute(
        """
        insert into llm_extraction (source, source_key, text_hash, model, prompt_version, result)
        values (%s, %s, %s, %s, %s, %s)
        on conflict (source, source_key) do update
        set text_hash = excluded.text_hash, model = excluded.model, prompt_version = excluded.prompt_version,
            result = excluded.result, created_at = now()
        """,
        (CACHE, dataset, text_hash(json.dumps(payload, sort_keys=True)), model, PROMPT_VERSION, Jsonb(result)))
    conn.commit()
    measured = usage(message)
    return result, {**measured, **llm.cost_report(model, measured)}


# ---- run


def suggest_table(conn, pack: Pack, dataset: str, c, *, exclude=frozenset(), claude: bool = False,
                  client=None, source: str | None = None, record_type: str | None = None, **claude_opts) -> dict:
    """Profile, rules (and Claude), and the draft mapping for one table. A folder of documents gets the documents
    mapping, whatever its columns look like."""
    prof = profile(c, pack, makers=known_manufacturers(conn, pack))
    rows_by_col: dict[str, list[str]] = {}
    for i, row in enumerate(c.rows()):
        if i >= SAMPLE_ROWS:
            break
        for k, v in row.items():
            if v not in (None, ""):
                rows_by_col.setdefault(k, []).append(str(v).strip())
    refs = references(conn, prof, rows_by_col, set(exclude), pick_key(prof))
    sug = rules(dataset, prof, refs, rows_by_col)
    for col, target in refs.items():
        if sug["columns"][col]["meaning"] == "ref":
            sug["columns"][col]["ref"] = target
    spent = {}
    if claude and any(v["meaning"] in ("code", "other") for v in sug["columns"].values()):
        payload = claude_input(dataset, prof, sug)
        result, spent = ask_claude(conn, dataset, payload, client=client, **claude_opts)
        sug = apply_claude(sug, result)
    if getattr(c, "kind", None) == "documents":
        first, _, rest = dataset.partition("_")
        what = {"file": ("other", "the document's file"), "section": ("other", "section number in the file (0: the document itself)"),
                "title": ("description", "the document's title"), "heading": ("description", "the section's heading"),
                "page": ("number", "page the section starts on"), "text": ("text", "the section's text")}
        cols = {k: ({"meaning": what[k][0], "by": "rules", "why": what[k][1]} if k in what else
                    {"meaning": "other", "by": "rules", "why": "about the file"}) for k in sug["columns"]}
        sug = {"columns": cols, "key": "file, section", "shape": "documents", "kind": "document_section"}
        return {"profile": prof, "suggestion": sug, "claude": spent,
                "mapping": document_draft(dataset, source or first, record_type or rest or "document")}
    return {"profile": prof, "suggestion": sug, "mapping": draft(dataset, sug, source, record_type), "claude": spent}


def save(conn, dataset: str, text: str, by: str) -> dict:
    """Approve a mapping for `dataset` as a new version in the catalog (the next ingest loads the table with it)
    and close its open review item. Raises ValueError when the YAML isn't a valid mapping for this dataset."""
    from . import catalog

    _, m = catalog._parse(text)
    if m.dataset != dataset:
        raise ValueError(f"mapping must keep dataset: {dataset}")
    catalog.propose(conn, text, by, note="saved from the review queue or Sources page", status="approved")
    conn.execute("update review_item set status = 'decided', decision = %s, decided_by = %s, decided_at = now() "
                 "where kind = 'mapping' and subject = %s and status = 'open'",
                 (Jsonb({"decision": "saved", "yaml": text}), by, dataset))
    conn.commit()
    return {"dataset": m.dataset, "mapping": f"{m.source}/{m.record_type}"}


def run(conn, pack: Pack, data_dir, *, claude: bool = False, client=None, datasets: list[str] | None = None,
        **claude_opts) -> dict:
    """Suggest mappings for every table in data_dir that has none, as `mapping` review items."""
    from . import ingest

    names = datasets or ingest.unmapped(data_dir, conn)
    out = {}
    for name in names:
        c = connectors.find(Path(data_dir), name)
        if c is None:
            continue
        s = suggest_table(conn, pack, name, c, claude=claude, client=client, **claude_opts)
        text = to_yaml(s["mapping"])
        payload = {"dataset": name, "connector": c.kind, "path": str(c.path), "schema": c.schema(),
                   "profile": s["profile"]["columns"], "suggestion": s["suggestion"], "yaml": text}
        conn.execute(
            """
            insert into review_item (kind, subject, payload) values ('mapping', %s, %s)
            on conflict (kind, subject) do update
            set payload = excluded.payload,
                status = case when review_item.payload->'schema' = excluded.payload->'schema'
                              and review_item.status <> 'stale' then review_item.status else 'open' end
            """, (name, Jsonb(payload)))
        conn.commit()
        out[name] = {"shape": s["suggestion"]["shape"], "key": s["suggestion"]["key"],
                     "by_claude": sum(1 for v in s["suggestion"]["columns"].values() if v["by"] == "claude"),
                     **({"claude": s["claude"]} if s["claude"] else {})}
    return out
