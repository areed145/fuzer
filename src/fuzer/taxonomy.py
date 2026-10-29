"""Equipment classes and models, derived after golden values are chosen.

The taxonomy (ISO 14224 by default, see standards/) gives `equipment_class` entities linked
`is_a` up to their category. The asset pack's classification rules decide each tag's class from
its ISA function letters, refined by its PI AF template or description; equipment takes the class
of the position it is installed at. `equipment_model` entities are manufacturer + model, with the
manufacturer normalised through the pack's aliases, so one model groups every pump of that make
across SAP, JDE and Maximo spellings. These are the anchors manuals and failure history attach to.

Everything here is a single pass over in-memory golden values: linear in the size of the asset.
"""

import re
from collections import Counter
from functools import cache
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process, utils

from .model import Candidate, Edge
from .pack import Pack

STANDARDS_DIR = Path(__file__).parent / "standards"
SOURCE = "fuzer"  # derived records are attributed to Fuzer itself


@cache
def load_standard(name: str) -> dict:
    return yaml.safe_load((STANDARDS_DIR / f"{name}.yaml").read_text())


class Taxonomy:
    def __init__(self, pack: Pack):
        rules = pack.classification
        self.name = rules.get("taxonomy", "iso14224")
        std = load_standard(self.name)
        self.prefix = self.name.upper().replace("ISO", "ISO")
        self.standard = std.get("standard", self.name)
        self.levels = std.get("levels", {})
        self.categories = std.get("categories", {})
        self.classes = {**std.get("classes", {}), **rules.get("extra_classes", {})}
        self.tag_functions = rules.get("tag_functions", {})
        self.templates = rules.get("templates", {})
        self.description_types = rules.get("description_types", {})
        self.aliases = {utils.default_process(k): v for k, v in rules.get("manufacturer_aliases", {}).items()}

    # keys: "ISO14224:PU" (class), "ISO14224:PU.CE" (type), "ISO14224:rotating" (category)
    def key(self, code: str) -> str:
        return f"{self.prefix}:{code}"

    def valid(self, code: str | None) -> bool:
        if not code:
            return False
        cls, _, typ = code.partition(".")
        return cls in self.classes and (not typ or typ in self.classes[cls].get("types", {}))

    def classify_tag(self, tag_key: str, template: str | None, description: str | None) -> str | None:
        """Class (or class.type) code for a tag key like 20-P-1001B."""
        parts = tag_key.split("-")
        code = self.tag_functions.get(parts[1]) if len(parts) >= 3 else None
        if template and self.valid(t := self.templates.get(template)) and (not code or t.split(".")[0] == code.split(".")[0]):
            code = t
        if code and "." not in code and description:
            types = self.classes.get(code, {}).get("types", {})
            words = description.lower()
            for word, typ in self.description_types.items():
                if typ in types and re.search(rf"\b{re.escape(word)}\b", words):
                    code = f"{code}.{typ}"
                    break
        return code if self.valid(code) else None

    def class_entities(self, used: set[str]) -> tuple[list[Candidate], list[Edge]]:
        """Entities for the used classes / types and everything above them, with is_a links."""
        cands, edges, seen = [], [], set()

        def emit(code, fields, parent):
            if code in seen:
                return
            seen.add(code)
            c = Candidate(SOURCE, "equipment_class", self.key(code), "equipment_class", self.key(code),
                          {"standard": self.standard, "code": code, **fields})
            if parent:
                c.edges.append(Edge(("ref", c.ref), "is_a", ("equipment_class", self.key(parent)), SOURCE))
            cands.append(c)

        for code in used:
            cls, _, typ = code.partition(".")
            info = self.classes[cls]
            emit(info["category"], {"description": self.categories.get(info["category"], info["category"]),
                                    "level": "category"}, None)
            emit(cls, {"description": info["name"], "level": "class",
                       **({"cfihos_class": info["cfihos"]} if info.get("cfihos") else {})}, info["category"])
            if typ:
                name = info["types"][typ]
                emit(code, {"description": f"{name} {info['name'].lower()}", "level": "type"}, cls)
        return cands, edges

    def manufacturer(self, raw: str | None, canonical: list[str]) -> str | None:
        if not raw or not (clean := utils.default_process(raw)):
            return None
        if clean in self.aliases:
            return self.aliases[clean]
        if canonical:
            hit = process.extractOne(clean, canonical, scorer=fuzz.token_set_ratio, processor=utils.default_process,
                                     score_cutoff=92)
            if hit:
                return hit[0]
        return raw.strip()


def normalise_model(model: str | None) -> str | None:
    if not model:
        return None
    return re.sub(r"\s+", " ", str(model).strip().upper()) or None


def derive(pack: Pack, kinds: dict[str, str], golden: dict[str, dict], tag_keys: dict[str, str],
           equipment_tags: dict[str, list[str]]) -> tuple[list[Candidate], list[Edge], list[tuple]]:
    """Classes, models and their links from golden values.

    kinds: fuzer_id -> kind; golden: fuzer_id -> {field: value}; tag_keys: tag fuzer_id -> key;
    equipment_tags: equipment fuzer_id -> tag fuzer_ids it is installed at (any time).
    Returns candidates, edges and extra field assertions (fuzer_id, field, value).
    """
    tax = Taxonomy(pack)
    edges: list[Edge] = []
    fields: list[tuple] = []
    used: set[str] = set()

    tag_class: dict[str, str] = {}
    for fid, key in tag_keys.items():
        g = golden.get(fid, {})
        if code := tax.classify_tag(key, g.get("template"), g.get("description")):
            tag_class[fid] = code
            used.add(code)
            edges.append(Edge(("id", fid), "classified_as", ("equipment_class", tax.key(code)), SOURCE))

    # Manufacturer names as the master system writes them are the canonical spellings.
    canonical = sorted({g["manufacturer"] for fid, g in golden.items()
                        if kinds.get(fid) == "equipment" and g.get("manufacturer")})
    canonical = sorted({tax.manufacturer(m, []) for m in canonical})
    models: dict[str, dict] = {}
    model_classes: dict[str, Counter] = {}
    for fid, kind in kinds.items():
        if kind != "equipment":
            continue
        g = golden.get(fid, {})
        classes = Counter(tag_class[t] for t in equipment_tags.get(fid, []) if t in tag_class)
        code = classes.most_common(1)[0][0] if classes else None
        if code:
            edges.append(Edge(("id", fid), "classified_as", ("equipment_class", tax.key(code)), SOURCE))
        mfr, model = tax.manufacturer(g.get("manufacturer"), canonical), normalise_model(g.get("model"))
        if mfr and model:
            mkey = f"{mfr.upper()}|{model}"
            models.setdefault(mkey, {"manufacturer": mfr, "model": model, "description": f"{mfr} {model}"})
            edges.append(Edge(("id", fid), "instance_of", ("equipment_model", mkey), SOURCE))
            if code:
                model_classes.setdefault(mkey, Counter())[code] += 1

    cands, class_edges = tax.class_entities(used)
    edges += class_edges
    for mkey, f in models.items():
        c = Candidate(SOURCE, "equipment_model", mkey, "equipment_model", mkey, f)
        if mkey in model_classes:
            code = model_classes[mkey].most_common(1)[0][0]
            c.edges.append(Edge(("ref", c.ref), "is_a", ("equipment_class", tax.key(code)), SOURCE))
        cands.append(c)

    for fid, kind in kinds.items():
        if level := tax.levels.get(kind):
            fields += [(fid, "iso14224_level", level["iso14224"]), (fid, "isa95_level", level["isa95"])]
    return cands, edges, fields
