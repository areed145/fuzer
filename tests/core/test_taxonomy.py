"""Equipment classes (ISO 14224) and models derived from golden values."""


import pytest

from fuzer.core.pack import load_pack
from fuzer.core.taxonomy import Taxonomy
from fuzer.graph import queries

PACK = load_pack()


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    c.stats = okan_db.built.stats
    yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def class_of(conn, ref):
    row = conn.execute(
        "select l.label from edge e join entity_label l on l.fuzer_id = e.dst where e.src = %s and e.rel = 'classified_as'",
        (fid(conn, ref),)).fetchone()
    return row and row["label"]


@pytest.mark.parametrize("tag,cls", [
    ("10-P-1001A", "ISO14224:PU.CE"),   # function P + AF template "Centrifugal Pump"
    ("20-K-2200B", "ISO14224:CO.CE"),
    ("20-V-2001", "ISO14224:VE.SE"),    # "Production Separator"
    ("20-V-2101B", "ISO14224:VE.SC"),    # "Compressor Suction Scrubber"
    ("10-V-2004", "ISO14224:VE.DR"),    # "Export Surge Drum"
    ("20-PT-3003", "ISO14224:IP.PR"),
    ("61-FT-3001", "ISO14224:IP.FL"),
])
def test_tag_classes(conn, tag, cls):
    assert class_of(conn, tag) == cls


def test_class_hierarchy_and_cfihos(conn):
    e = queries.entity(conn, fid(conn, "ISO14224:PU"))
    assert e["golden"]["cfihos_class"]["value"] == "pump" and e["golden"]["level"]["value"] == "class"
    up = {x["other_label"] for x in e["edges"] if x["rel"] == "is_a" and x["direction"] == "out"}
    assert up == {"ISO14224:rotating"}


def test_equipment_class_and_model(conn):
    tag = fid(conn, "10-P-1001A")
    pumps = [r["src"] for r in conn.execute("select src from edge where dst = %s and rel = 'installed_at'", (tag,))]
    assert len(pumps) == 1
    models = {conn.execute("select l.label from edge e join entity_label l on l.fuzer_id = e.dst "
                           "where e.src = %s and e.rel = 'instance_of'", (p,)).fetchone()["label"] for p in pumps}
    assert len(models) == 1
    model = fid(conn, next(iter(models)).replace(" ", "|"))
    assert queries.members(conn, model)["total"] >= 1


def test_manufacturer_spellings_share_a_model(conn):
    makers = {r["m"] for r in conn.execute(
        "select g.value #>> '{}' as m from golden g join entity e using (fuzer_id) "
        "where e.kind = 'equipment_model' and g.field = 'manufacturer'")}
    assert not makers & {"BHGE", "K.S.B.", "Flowsrve", "SULZER PUMPS"}  # aliases folded into one name
    assert conn.stats["models"] < conn.stats["entities"] / 10  # a fleet has few models


def test_members_include_subtypes(conn):
    pumps = queries.members(conn, fid(conn, "ISO14224:PU"), kind="tag")
    centrifugal = queries.members(conn, fid(conn, "ISO14224:PU.CE"), kind="tag")
    assert pumps["total"] >= centrifugal["total"] > 0
    instruments = queries.members(conn, fid(conn, "ISO14224:IP"), limit=5)
    assert instruments["total"] > 40 and len(instruments["items"]) == 5


def test_hierarchy_levels(conn):
    g = queries.entity(conn, fid(conn, "OKAN"))["golden"]
    assert g["iso14224_level"]["value"] == "installation" and g["isa95_level"]["value"] == "site"


def test_entity_links_are_capped_with_totals(conn):
    e = queries.entity(conn, fid(conn, "ISO14224:IP"), per_group=10)
    members = [x for x in e["edges"] if x["rel"] == "is_a" and x["direction"] == "in"]
    assert len(members) <= 10 and e["edge_counts"]["in:is_a"] >= len(members)


def test_pack_extensions():
    pack = load_pack()
    pack.classification = {**pack.classification, "extra_classes": {
        "FH": {"name": "Fired heaters", "category": "mechanical", "types": {"BX": "Box"}}},
        "tag_functions": {**pack.classification["tag_functions"], "H": "FH.BX"}}
    tax = Taxonomy(pack)
    assert tax.classify_tag("31-H-1001", None, None) == "FH.BX"  # a pack can extend its classification
    assert tax.classify_tag("31-ZZ-1001", None, None) is None
