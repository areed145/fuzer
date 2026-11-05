"""The column mapper (#225): every built-in mapping reads into the form and writes back to the same mapping;
form edits (a field kept, renamed or typed, a column linked to a tag or well, the key) land in the YAML, and
what the form doesn't show is kept."""

import json

import pytest
import yaml

from fuzer.sources import catalog, mapper


def _norm(raw: dict) -> dict:
    """A mapping with its fields and links as sets, so the order the form writes them in doesn't matter."""
    raw = dict(raw)
    ent = dict(raw.get("entity") or {})
    if isinstance(ent.get("fields"), list):
        def one(f):  # "x", [x, x] and [x, {from: x}] say the same
            if isinstance(f, str):
                return [f, f]
            if isinstance(f, list) and isinstance(f[1], dict) and set(f[1]) == {"from"}:
                return [f[0], f[1]["from"]]
            return f
        ent["fields"] = sorted(json.dumps(one(f), sort_keys=True) for f in ent["fields"])
    if ent:
        raw["entity"] = ent
    if raw.get("links"):
        raw["links"] = sorted(json.dumps(x, sort_keys=True) for x in raw["links"])
    if isinstance(raw.get("key"), list) and len(raw["key"]) == 1:
        raw["key"] = raw["key"][0]
    return raw


@pytest.mark.parametrize("raw,text", catalog.seed_files(), ids=lambda x: x["dataset"] if isinstance(x, dict) and
                         x.get("dataset") else (f"{x['source']}_{x['record_type']}" if isinstance(x, dict) else ""))
def test_builtin_mappings_round_trip(raw, text):
    f = mapper.form(text)
    again = mapper.to_yaml(f, text)
    assert _norm(yaml.safe_load(again)) == _norm(raw)


def test_form_edits_land_in_the_yaml():
    text = next(t for r, t in catalog.seed_files() if r.get("dataset") == "sap_equipment")
    f = mapper.form(text, ["equnr", "serial", "floc", "description", "manufacturer", "model", "weight_kg", "valid_from"])
    cols = {c["name"]: c for c in f["columns"]}
    assert cols["model"]["field"] == {"name": "model", "as": None} and cols["floc"]["link"]["target"] == "tag"
    assert cols["weight_kg"]["field"] is None and f["kind"] == "equipment" and f["entity_key"] == {"column": "serial", "as": "serial"}
    cols["model"]["field"] = None                                      # drop a field
    cols["weight_kg"]["field"] = {"name": "weight", "as": "number"}    # keep a new one, typed
    cols["description"]["field"]["name"] = "description"
    cols["equnr"]["link"] = {"rel": "same_as", "target": "equipment", "keep": {}}  # a new link
    out = yaml.safe_load(mapper.to_yaml(f, text))
    fields = out["entity"]["fields"]
    assert "model" not in fields and ["weight", {"from": "weight_kg", "as": "number"}] in fields
    assert out["entity"]["block"] == {"from": "floc", "as": "tag"}  # not on the form: kept
    assert {"rel": "same_as", "to": {"kind": "equipment", "from": "equnr", "as": "serial"}} in out["links"]
    old = next(x for x in out["links"] if x["rel"] == "installed_at")
    assert old["valid_from"] == "valid_from"  # a linked column keeps its link's other settings
    assert mapper.to_yaml(f, text).startswith("# ")  # the comment at the top stays


def test_what_the_form_cannot_show_is_kept():
    text = next(t for r, t in catalog.seed_files() if r.get("hooks") or r.get("measurements"))
    f = mapper.form(text)
    assert f["kept"]
    assert _norm(yaml.safe_load(mapper.to_yaml(f, text))) == _norm(yaml.safe_load(text))
    with pytest.raises(catalog.CatalogError):
        mapper.to_yaml({**f, "shape": "nonsense"}, text)
