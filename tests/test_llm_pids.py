"""Claude reading scanned P&IDs, with a fake client (no network, no cost)."""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fuzer import db, fakedata, ingest, llm_pids, pipeline
from fuzer.pack import load_pack

PACK = load_pack()
SHEET = "OSB-44-PID-001"


class FakeVision:
    """Reads the drawing the way a good but imperfect reader would: one misread character, one tag
    that isn't in the register, the line numbers, and one bubble missed."""

    def __init__(self, truth):
        self.truth, self.requests = truth, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        content = kw["messages"][0]["content"]
        assert sum(1 for b in content if b["type"] == "image") == 4  # 2200 x 1500 -> 2 x 2 tiles
        tags = []
        for key in self.truth[SHEET]:
            if key == "44-TT-2001":
                continue  # missed
            text = key if key.split("-")[1] in ("P", "V", "K", "E") else key.split("-", 1)[1]  # bubbles have no area
            if key == "44-VT-1001B":
                text = "VT-1OO1B"  # O for 0: not a tag
            tags.append({"text": text, "kind": "equipment" if text == key else "instrument", "confidence": 0.9})
        tags += [{"text": '6"-P-44479-A1', "kind": "line", "confidence": 0.8},
                 {"text": "PT-9999", "kind": "instrument", "confidence": 0.6}]
        data = {"title_block": {"drawing_number": SHEET, "title": "AREA 44 - WATER INJECTION", "revision": "D"},
                "tags": tags}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(data))],
                               usage=SimpleNamespace(input_tokens=9000, output_tokens=400, cache_read_input_tokens=0))


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
        yield c, json.loads((data / "pid_truth.json").read_text())


def test_image_is_ingested_with_fingerprint(setup):
    conn, _ = setup
    p = conn.execute("select payload from source_record where source = 'pid' and source_key = %s", (SHEET,)).fetchone()["payload"]
    assert Path(p["image_path"]).is_file() and len(p["image_sha256"]) == 64


def test_tiles_stay_within_size_limit(setup):
    conn, _ = setup
    p = conn.execute("select payload from source_record where source_key = %s", (SHEET,)).fetchone()["payload"]
    from PIL import Image
    import io
    t = llm_pids.tiles(p["image_path"])
    assert len(t) == 4 and all(max(Image.open(io.BytesIO(png)).size) <= llm_pids.MAX_EDGE for _, png in t)


def test_read_link_and_score(setup):
    conn, truth = setup
    fake = FakeVision(truth)
    out = llm_pids.run(conn, PACK, client=fake, workers=1)
    assert out["stored"] == 1 and out["failed_requests"] == 0
    assert out["tags_linked"] == 10 and out["findings"] == 2  # VT-1OO1B (not a tag), 44-PT-9999 (not in register)
    pipeline.rebuild(conn, PACK)

    s = llm_pids.score(conn, truth)
    assert s["recall"] == round(10 / 12, 3) and s["precision"] == 1.0
    assert set(s["problems"][0]["missed"]) == {"44-TT-2001", "44-VT-1001B"}

    result = conn.execute("select result from llm_extraction where source = 'pid'").fetchone()["result"]
    issues = {f["issue"] for f in result["findings"]}
    assert issues == {"not a tag", "not in register"} and result["lines"] == ['6"-P-44479-A1']


def test_cached_until_the_scan_changes(setup):
    conn, truth = setup
    fake = FakeVision(truth)
    assert llm_pids.run(conn, PACK, client=fake)["sheets"] == 0 and fake.requests == []
    conn.execute(
        "update source_record set payload = jsonb_set(payload, '{image_sha256}', '\"rescanned\"') where source_key = %s",
        (SHEET,))
    conn.commit()
    pipeline.rebuild(conn, PACK)  # stale reading no longer applied
    assert llm_pids.score(conn, truth)["sheets_read"] == 0


def test_image_endpoint(setup):
    conn, _ = setup
    from fuzer.api import app
    fid = conn.execute("select fuzer_id from crosswalk where source = 'pid' and source_key = %s", (SHEET,)).fetchone()["fuzer_id"]
    r = TestClient(app).get(f"/documents/{fid}/image")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
