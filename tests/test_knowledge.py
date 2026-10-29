"""Scoped search across manuals, guides, procedures and incidents."""

import os
import tempfile

import pytest

from fakeplant import offshore
from fuzer import db, ingest, knowledge, pipeline, queries
from fuzer.pack import load_pack

PACK = load_pack()


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = tempfile.mkdtemp(prefix="fzdata")
    offshore.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_scope_reaches_model_class_and_area(conn):
    sc = knowledge.scope(conn, fid(conn, "20-P-1001B"))
    how = {v[1] for v in sc.values()}
    assert {"this", "installed", "model", "class", "parent"} <= how
    labels = {r["label"] for r in conn.execute("select label from entity_label where fuzer_id = any(%s)", (list(sc),))}
    assert {"ISO14224:PU.CE", "ISO14224:PU", "ISO14224:rotating", "OSB-20"} <= labels


def test_documents_for_a_pump(conn):
    docs = knowledge.documents_for(conn, fid(conn, "20-P-1001B"))
    types = {d["doc_type"]: d for d in docs}
    assert types["oem_manual"]["why"] == "model"       # its model's manual
    assert types["procedure"]["why"] == "class"         # centrifugal pump changeover
    numbers = [d["label"].split(" rev ")[0] for d in docs]
    assert len(numbers) == len(set(numbers))  # one (current) revision per document


def test_scoped_search_finds_the_right_manual_page(conn):
    hits = knowledge.search(conn, "what causes high vibration on this pump?", fid(conn, "20-P-1001B"))
    top = hits[0]
    assert top["kind"] == "document_section" and "Troubleshooting" in top["heading"]
    assert "[[vibration]]" in top["snippet"].lower() and top["why"].startswith("model")
    pump_manual = top["document_label"]
    # Unscoped, other manuals compete; scoped, every hit relates to this pump.
    assert all(h["why"] for h in hits)
    assert pump_manual


def test_site_guide_ranks_for_its_tags(conn):
    hits = knowledge.search(conn, "level transmitter disagrees with gauge glass", fid(conn, "20-LT-2001"))
    assert hits and hits[0]["document_label"] == "TG-20-001 rev A"


def test_superseded_revisions_left_out(conn):
    all_hits = knowledge.search(conn, "record vibration spectra bearing change", include_superseded=True, limit=50)
    current = knowledge.search(conn, "record vibration spectra bearing change", limit=50)
    assert {h["status"] for h in current} <= {"current"}
    assert len(all_hits) >= len(current)


def test_api_and_unknown_ref(conn):
    from fastapi.testclient import TestClient
    from fuzer.api import app
    client = TestClient(app)
    r = client.get("/knowledge/search", params={"q": "seal leak", "ref": "44-P-1001B"})
    assert r.status_code == 200 and r.json()
    assert client.get("/knowledge/search", params={"q": "seal", "ref": "ZZ-NOPE-9"}).status_code == 404
