"""A second, very different asset (onshore pads) runs end to end with only a new pack."""

import os
import tempfile

import pytest

from fakeplant import onshore
from fuzer import db, ingest, pipeline, queries
from fuzer.pack import load_pack

PACK = load_pack("onshore_demo")


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    onshore.generate(data, pads=6, wells_per_pad=8, logs=200)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        c.stats = pipeline.rebuild(c, PACK)
        c.data = data
        yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_wells_tags_and_classes(conn):
    assert conn.stats["entities"] > 500
    well = queries.entity(conn, fid(conn, "RB 03-05H"))
    assert well["kind"] == "well" and {"RBF.RB0305H.TBG", "RBF.RB0305H.OIL"} <= {
        x["other_label"] for x in well["edges"] if x["rel"] == "has_timeseries"}
    esp = queries.entity(conn, fid(conn, "03-ESP-305"))
    assert [x["other_label"] for x in esp["edges"] if x["rel"] == "classified_as"] == ["ISO14224:ESP"]
    cls = queries.entity(conn, fid(conn, "ISO14224:ESP"))["golden"]
    assert cls["description"]["value"] == "Electric submersible pumps"  # pack-added class


def test_flow_network_and_downtime(conn):
    sep = fid(conn, "03-SEP-101")
    wells = {r["label"] for r in queries.neighborhood(conn, sep, rels=["flows_to"])["nodes"] if r["kind"] == "well"}
    assert len(wells) == 8
    by_class = queries.downtime_ranking(conn, "class")
    assert by_class and by_class[0]["label"] == "ISO14224:ESP"


def test_logs_link_onshore_names(conn):
    hits = queries.link_text(conn, PACK, "ESP on RB 02-07H tripped, 02-TK-201A high level")
    assert [(h["key"], bool(h["fuzer_id"])) for h in hits] == [("RB 02-07H", True), ("02-TK-201A", True)]


def test_optional_sources_absent(conn):
    sources = {r["source"] for r in conn.execute("select distinct source from source_record")}
    assert "jde" not in sources and "maximo" not in sources



def test_wellheads_find_their_pads(conn):
    import json
    from pathlib import Path

    from fuzer import evaluate

    truth = json.loads((Path(conn.data) / "answer_keys" / "spatial.json").read_text())
    assert len(truth) == 48 and conn.stats["spatial_links"] == {"well->area": 48}
    s = evaluate.spatial(conn, PACK, truth)
    assert s["recall"] == 1.0 and s["precision"] == 1.0
