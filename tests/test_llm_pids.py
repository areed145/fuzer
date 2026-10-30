"""Claude reading scanned P&IDs, with a fake client (no network, no cost)."""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fakeplant import offshore
from fuzer import db, ingest, llm_pids, pipeline
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
        sheet = next(b["text"] for b in content if b["type"] == "text").split()[1].rstrip(".")
        truth = self.truth[sheet]
        if sheet != SHEET:  # other drawings: read perfectly, in the top-left tile
            tags = [{"text": x["key"], "kind": x["kind"], "confidence": 0.9, "tile": 1, "x": 10, "y": 10}
                    for x in truth["tags"]]
            data = {"title_block": {"drawing_number": sheet, "title": "", "revision": ""}, "tags": tags,
                    "connections": [{"from": ln["from"], "to": ln["to"], "line": ln["text"]} for ln in truth["lines"]]}
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(data))],
                                   usage=SimpleNamespace(input_tokens=9000, output_tokens=400, cache_read_input_tokens=0))
        tags = []
        # Positions as Claude gives them: a tile number and pixels within that tile (tiles here aren't shrunk).
        where = {x["key"]: x for x in truth["tags"]}
        for key in where:
            if key == "44-TT-2001":
                continue  # missed
            text = key if key.split("-")[1] in ("P", "V", "K", "E") else key.split("-", 1)[1]  # bubbles have no area
            if key == "44-VT-1001B":
                text = "VT-1OO1B"  # O for 0: not a tag
            x, y = where[key]["x"], where[key]["y"]
            boxes = llm_pids.tile_boxes(2200, 1500)
            tile = next(i for i, b in enumerate(boxes, 1) if b[0] <= x < b[2] and b[1] <= y < b[3])
            box = boxes[tile - 1]
            tags.append({"text": text, "kind": "equipment" if text == key else "instrument", "confidence": 0.9,
                         "tile": tile, "x": x - box[0], "y": y - box[1]})
        tags += [{"text": '6"-P-44479-A1', "kind": "line", "confidence": 0.8, "tile": 2, "x": 400, "y": 300},
                 {"text": "PT-9999", "kind": "instrument", "confidence": 0.6, "tile": 3, "x": 400, "y": 300}]
        data = {"title_block": {"drawing_number": SHEET, "title": "AREA 44 - WATER INJECTION", "revision": "D"},
                "tags": tags, "connections": [{"from": ln["from"], "to": ln["to"], "line": ln["text"]}
                                              for ln in truth["lines"]]}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(data))],
                               usage=SimpleNamespace(input_tokens=9000, output_tokens=400, cache_read_input_tokens=0))


@pytest.fixture(scope="module")
def setup():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzdata"))
    offshore.generate(data)
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
    assert len(t) == 4 and all(max(Image.open(io.BytesIO(png)).size) <= llm_pids.MAX_EDGE for _, png, _ in t)
    assert [box for _, _, box in t] == llm_pids.tile_boxes(2200, 1500)


def test_every_drawn_sheet_but_smart_is_read(setup):
    conn, truth = setup
    formats = {r["source_key"]: r["f"] for r in conn.execute(
        "select source_key, payload->>'format' as f from source_record where source = 'pid'")}
    assert set(truth) == set(formats)  # every sheet is drawn
    assert llm_pids.run(conn, PACK, dry_run=True)["sheets"] == sum(1 for f in formats.values() if f != "smart")


def test_read_link_and_score(setup):
    conn, truth = setup
    fake = FakeVision(truth)
    out = llm_pids.run(conn, PACK, client=fake, workers=1)
    assert out["failed_requests"] == 0 and out["stored"] == len(fake.requests) == 2  # the scan and the text PDF
    pipeline.rebuild(conn, PACK)

    result = conn.execute("select result from llm_extraction where source = 'pid' and source_key = %s",
                          (SHEET,)).fetchone()["result"]
    assert len(result["mentions"]) == 10
    issues = {f["issue"] for f in result["findings"] if f["kind"] not in ("connection", "title_block")}
    assert issues == {"not a tag", "not in register"} and result["lines"][0]["text"] == '6"-P-44479-A1'
    # Positions come back in sheet pixels, close to where the generator drew each tag.
    drawn = {x["key"]: x for x in truth[SHEET]["tags"]}
    for m in result["mentions"]:
        assert abs(m["x"] - drawn[m["key"]]["x"]) <= 2 and abs(m["y"] - drawn[m["key"]]["y"]) <= 2
    assert result["size"] == [2200, 1500]

    s = llm_pids.score(conn, {SHEET: truth[SHEET]})
    assert s["recall"] == round(10 / 12, 3) and s["precision"] == 1.0
    assert set(s["problems"][0]["missed"]) == {"44-TT-2001", "44-VT-1001B"}
    assert s["piping"] == {"recall": 1.0, "precision": 1.0}


def test_drawing_endpoint_places_readings(setup):
    conn, truth = setup
    from fuzer.api import app
    client = TestClient(app)
    fid = conn.execute("select fuzer_id from crosswalk where source = 'pid' and source_key = %s", (SHEET,)).fetchone()["fuzer_id"]
    d = client.get(f"/documents/{fid}/drawing").json()
    assert (d["width"], d["height"], d["format"]) == (2200, 1500, "image") and d["read_by"]
    status = {m["status"] for m in d["marks"]}
    assert status == {"linked", "not_a_tag", "not_in_register", "line"}
    linked = [m for m in d["marks"] if m["status"] == "linked"]
    assert all(m["id"] and "x" in m for m in linked)
    assert d["connections"] and all(c["from_id"] and c["to_id"] for c in d["connections"])

    smart = conn.execute("select c.fuzer_id from crosswalk c join source_record r using (source, record_type, source_key) "
                         "where c.source = 'pid' and r.payload->>'format' = 'smart' limit 1").fetchone()["fuzer_id"]
    s = client.get(f"/documents/{smart}/drawing").json()
    assert s["read_by"] == "smart file" and s["marks"] and all("x" in m for m in s["marks"])


def test_cached_until_the_scan_changes(setup):
    conn, truth = setup
    fake = FakeVision(truth)
    assert llm_pids.run(conn, PACK, client=fake)["sheets"] == 0 and fake.requests == []
    truth = {SHEET: truth[SHEET]}
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
