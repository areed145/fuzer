"""A CFIHOS handover (#63): the contractor's tag, equipment and document registers for Harrier's vapour recovery unit
load into the same tags and equipment SAP knows, give each tag its class from the CFIHOS class name (the speed
transmitter, whose function code the pack doesn't map, gets its class only from the handover), and add the
handed-over documents with the tags they cover."""

from fuzer.sources import packs


def _id(conn, kind, key):
    rows = conn.execute("select k.fuzer_id from entity_key k join entity e on e.fuzer_id = k.fuzer_id "
                        "where k.kind = %s and k.key = %s and e.merged_into is null", (kind, key)).fetchall()
    assert len(rows) == 1, (kind, key, rows)
    return rows[0]["fuzer_id"]


def _class(conn, fid):
    rows = conn.execute("select k.key from edge x join entity_key k on k.fuzer_id = x.dst "
                        "where x.src = %s and x.rel = 'classified_as' and k.kind = 'equipment_class'", (fid,)).fetchall()
    return sorted(r["key"] for r in rows)


def test_the_cfihos_pack_names_its_four_tables():
    import yaml

    pack = yaml.safe_load(packs.builtin("cfihos"))
    assert [d["name"] for d in pack["datasets"]] == ["cfihos_tag", "cfihos_equipment", "cfihos_document",
                                                    "cfihos_tag_document"]


def test_a_cfihos_handover_loads_into_classes_tags_and_documents(harrier_db):
    conn = harrier_db
    counts = {r["record_type"]: r["n"] for r in conn.execute(
        "select record_type, count(*) as n from source_record where source = 'cfihos' group by 1")}
    assert counts == {"tag": 10, "equipment": 3, "document": 4, "tag_document": 12}

    # The handover's tags are SAP's tags, and its equipment SAP's equipment (matched on serial), not new ones.
    compressor = _id(conn, "tag", "G1-K-7001")
    assert conn.execute("select count(*) as n from entity where kind = 'tag' and merged_into is null "
                        "and fuzer_id in (select fuzer_id from entity_key where key like 'G1-%%')").fetchone()["n"] == 14  # SAP's 14 G1 tags
    g = {r["field"]: r["value"] for r in conn.execute("select field, value from golden where fuzer_id = %s", (compressor,))}
    assert g["cfihos_class"] == "reciprocating compressor" and g["description"] == "Vapour Recovery Compressor"
    assert _class(conn, compressor) == ["ISO14224:CO.RE"]
    assert _class(conn, _id(conn, "tag", "G1-E-7003")) == ["ISO14224:HE.AC"]
    assert _class(conn, _id(conn, "tag", "G1-V-7002")) == ["ISO14224:VE.SC"]
    assert _class(conn, _id(conn, "tag", "G1-ST-7008")) == ["ISO14224:IP.SP"]  # no class from its function code
    ariel = conn.execute("select distinct e.src from edge e where e.rel = 'installed_at' and e.dst = %s",
                         (compressor,)).fetchall()
    assert len(ariel) == 1 and _class(conn, ariel[0]["src"]) == ["ISO14224:CO.RE"]

    sheet = _id(conn, "document", "HR-G1-GA-7000 rev 1")
    covers = conn.execute("select k.key from edge x join entity_key k on k.fuzer_id = x.dst and k.kind = 'tag' "
                          "where x.src = %s and x.rel = 'applies_to'", (sheet,)).fetchall()
    assert sorted(r["key"] for r in covers) == ["G1-E-7003", "G1-K-7001", "G1-V-7002"]
    doc = {r["field"]: r["value"] for r in conn.execute("select field, value from golden where fuzer_id = %s", (sheet,))}
    assert doc["title"] == "Vapour recovery package general arrangement" and doc["doc_type"] == "drawing"
