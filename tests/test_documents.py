"""Documents (EDMS manifest + files): revisions, sections, metadata links, file serving."""

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fuzer import db, fakedata, ingest, pipeline, queries
from fuzer.documents import sections
from fuzer.pack import load_pack

PACK = load_pack()


@pytest.fixture(scope="module")
def setup():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzdata"))
    fakedata.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c, data, json.loads((data / "documents.json").read_text())


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def test_pdf_and_markdown_sections(setup):
    _, data, manifest = setup
    pdf = next(d for d in manifest if d["file"].endswith(".pdf") and d["doc_type"] == "oem_manual")
    secs, facts = sections(data / pdf["file"])
    assert facts["format"] == "pdf" and facts["pages"] == 7
    trouble = next(s for s in secs if s["heading"] == "5 Troubleshooting")
    assert trouble["page"] == 5
    assert any(word in trouble["text"].lower() for word in ("vibration", "temperature", "leak", "level"))
    md = next(d for d in manifest if d["file"].endswith(".md") and d["doc_type"] == "oem_manual")
    assert [s["heading"] for s in sections(data / md["file"])[0]][:2] == ["Safety", "Description"]


def test_revisions_and_supersession(setup):
    conn, _, manifest = setup
    revised = next(d for d in manifest if d.get("supersedes"))
    new = queries.entity(conn, fid(conn, f"{revised['doc_number']} rev {revised['revision']}"))
    assert new["golden"]["status"]["value"] == "current"
    old = [x for x in new["edges"] if x["rel"] == "supersedes" and x["direction"] == "out"]
    assert old and old[0]["other_label"] == f"{revised['doc_number']} rev {revised['supersedes']}"
    assert queries.documents_with_several_current_revisions(conn) == []


def test_manual_applies_to_its_model_and_the_pumps_of_that_model(setup):
    conn, _, manifest = setup
    manual = next(d for d in manifest if d["doc_type"] == "oem_manual" and d["status"] == "current")
    doc = fid(conn, f"{manual['doc_number']} rev {manual['revision']}")
    model = conn.execute("select dst from edge where src = %s and rel = 'applies_to'", (doc,)).fetchone()["dst"]
    assert conn.execute("select kind from entity where fuzer_id = %s", (model,)).fetchone()["kind"] == "equipment_model"
    assert queries.members(conn, model)["total"] >= 1  # pumps of that model


def test_guides_and_procedures_link_by_tag_and_class(setup):
    conn, _, _ = setup
    guide = queries.entity(conn, fid(conn, "TG-20-001 rev A"))
    assert {"20-V-2001", "20-LT-2001"} <= {x["other_label"] for x in guide["edges"] if x["rel"] == "applies_to"}
    procedure = queries.entity(conn, fid(conn, "PR-PU-001 rev A"))
    assert [x["other_label"] for x in procedure["edges"] if x["rel"] == "applies_to"] == ["ISO14224:PU.CE"]


def test_sections_are_entities_that_mention_tags(setup):
    conn, _, _ = setup
    guide = fid(conn, "TG-23-001 rev A")
    secs = conn.execute("select src from edge where dst = %s and rel = 'part_of'", (guide,)).fetchall()
    assert len(secs) >= 2
    mentioned = {r["label"] for r in conn.execute(
        "select l.label from edge e join entity_label l on l.fuzer_id = e.dst "
        "where e.src = any(%s) and e.rel = 'mentions'", ([s["src"] for s in secs],))}
    assert {"23-K-1001A", "23-E-3001A"} <= mentioned


def test_file_endpoint_and_search(setup):
    conn, _, _ = setup
    from fuzer.api import app
    client = TestClient(app)
    r = client.get(f"/documents/{fid(conn, 'PR-CO-001 rev A')}/file")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert any(h["kind"] == "document_section" for h in queries.search(conn, PACK, "seal flush"))
