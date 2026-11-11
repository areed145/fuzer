"""Claude reading scanned P&IDs, with a fake client (no network, no cost)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fuzer.core.pack import load_pack
from fuzer.ops import pipeline
from fuzer.sources import llm_pids

PACK = load_pack("okan")
SHEET = "OKAN-20-PID-005"


class FakeVision:
    """Reads the drawing the way a good but imperfect reader would: one misread character, one tag
    that isn't in the register, the line numbers, and one bubble missed."""

    def __init__(self, truth):
        self.truth, self.requests = truth, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    @staticmethod
    def topology(sheet, truth):
        """Lines, off-page ends and loops as drawn; on OKAN-20-PID-006 (train C) it misses the off-page end, so
        the inlet separator's line to train C is shown by one sheet only."""
        offpage = [{k: o[k] for k in ("direction", "sheet", "tag", "other", "line")} for o in truth["offpage"]
                   if sheet != "OKAN-20-PID-006"]
        return {"connections": [{"from": c["from"], "to": c["to"], "line": c["line"], "via": c["via"], "stream": ""}
                                for c in truth["connections"] if c.get("to") and not c.get("offpage")],
                "offpage": offpage, "loops": [{"members": lp["members"]} for lp in truth["loops"]],
                "tables": [{"title": t["title"], "columns": t["columns"],
                            "rows": [r["cells"] for r in t["rows"] if not r.get("hidden")]} for t in truth["tables"]],
                "notes": [{"number": n["n"], "text": n["text"]} for n in truth["notes"] if not n.get("hidden")]}

    def create(self, **kw):
        self.requests.append(kw)
        content = kw["messages"][0]["content"]
        sheet = next(b["text"] for b in content if b["type"] == "text").split()[1].rstrip(".")
        truth = self.truth[sheet]
        width, height = truth["size"]
        assert sum(1 for b in content if b["type"] == "image") == len(llm_pids.tile_boxes(width, height))
        if truth.get("kind") == "pfd":  # a PFD: typical tags as drawn, streams by number
            drawn = list(dict.fromkeys(x.get("as_drawn", x["key"]) for x in truth["tags"]))
            data = {"title_block": {"drawing_number": sheet, "title": "", "revision": "", "date": truth["date"]},
                    "tags": [{"text": t, "kind": "equipment", "confidence": 0.9, "tile": 1, "x": 10, "y": 10, "on": ""}
                             for t in drawn],
                    "connections": [{"from": st["from"], "to": st["to"], "line": "", "via": [], "stream": str(st["n"])}
                                    for st in truth["streams"]],
                    "offpage": [], "loops": [], **{k: v for k, v in self.topology(sheet, truth).items()
                                                   if k in ("tables", "notes")}}
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(data))],
                                   usage=SimpleNamespace(input_tokens=9000, output_tokens=400, cache_read_input_tokens=0))
        if sheet != SHEET:  # other drawings: read perfectly, in the top-left tile
            tags = [{"text": x["key"], "kind": x["kind"], "confidence": 0.9, "tile": 1, "x": 10, "y": 10,
                     "on": x.get("on") or ""} for x in truth["tags"]]
            tags += [{"text": ln, "kind": "line", "confidence": 0.9, "tile": 1, "x": 10, "y": 10, "on": ""}
                     for ln in dict.fromkeys(c["line"] for c in truth["connections"] if c.get("line"))]
            data = {"title_block": {"drawing_number": sheet, "title": "", "revision": "", "date": truth.get("date", "")}, "tags": tags,
                    **self.topology(sheet, truth)}
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(data))],
                                   usage=SimpleNamespace(input_tokens=9000, output_tokens=400, cache_read_input_tokens=0))
        tags = []
        # Positions as Claude gives them: a tile number and pixels within that tile (tiles here aren't shrunk).
        where = {x["key"]: x for x in truth["tags"]}
        for key, drawn in where.items():
            if key == "20-TT-2200B":
                continue  # missed
            text = key if key.split("-")[1] in ("P", "V", "K", "E") else key.split("-", 1)[1]  # bubbles have no area
            if key == "20-VT-2200B":
                text = "VT-1OO1B"  # O for 0: not a tag
            x, y = drawn["x"], drawn["y"]
            boxes = llm_pids.tile_boxes(width, height)
            tile = next(i for i, b in enumerate(boxes, 1) if b[0] <= x < b[2] and b[1] <= y < b[3])
            box = boxes[tile - 1]
            tags.append({"text": text, "kind": "equipment" if text == key else "instrument", "confidence": 0.9,
                         "tile": tile, "x": x - box[0], "y": y - box[1], "on": drawn.get("on") or ""})
        tags += [{"text": '6"-P-44479-A1', "kind": "line", "confidence": 0.8, "tile": 2, "x": 400, "y": 300, "on": ""},
                 {"text": "PT-9999", "kind": "instrument", "confidence": 0.6, "tile": 3, "x": 400, "y": 300, "on": ""}]
        data = {"title_block": {"drawing_number": SHEET, "title": "AREA 20 - TRAIN B", "revision": "D",
                                "date": truth["date"]},
                "tags": tags, **self.topology(sheet, truth)}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(data))],
                               usage=SimpleNamespace(input_tokens=9000, output_tokens=400, cache_read_input_tokens=0))


@pytest.fixture(scope="module")
def setup(okan_db):
    c = okan_db.conn
    data = okan_db.data
    yield c, json.loads((data / "pid_truth.json").read_text())


@pytest.fixture(scope="module")
def read(setup):
    """The drawings read once by the fake Claude, and the register rebuilt with the readings: (result, fake)."""
    conn, truth = setup
    fake = FakeVision(truth)
    out = llm_pids.run(conn, PACK, client=fake, workers=1)
    pipeline.rebuild(conn, PACK)
    return out, fake


def test_image_is_ingested_with_fingerprint(setup):
    conn, _ = setup
    p = conn.execute("select payload from source_record where source = 'pid' and source_key = %s", (SHEET,)).fetchone()["payload"]
    assert Path(p["image_path"]).is_file() and len(p["image_sha256"]) == 64


def test_tiles_stay_within_size_limit(setup):
    conn, truth = setup
    p = conn.execute("select payload from source_record where source_key = %s", (SHEET,)).fetchone()["payload"]
    import io

    from PIL import Image
    t = llm_pids.tiles(p["image_path"])
    assert len(t) == 9 and all(max(Image.open(io.BytesIO(png)).size) <= llm_pids.MAX_EDGE for _, png, _ in t)
    assert [box for _, _, box in t] == llm_pids.tile_boxes(*truth[SHEET]["size"])  # a two-band train sheet with its data tables: 3 x 3


def test_every_drawn_sheet_but_smart_is_read(setup):
    conn, truth = setup
    formats = {r["source_key"]: r["f"] for r in conn.execute(
        "select source_key, payload->>'format' as f from source_record where source = 'pid'")}
    assert set(truth) == set(formats)  # every sheet is drawn
    assert llm_pids.run(conn, PACK, dry_run=True, force=True)["sheets"] == sum(1 for f in formats.values() if f != "smart")


def test_read_link_and_score(setup, read):
    conn, truth = setup
    out, fake = read
    assert out["failed_requests"] == 0 and out["stored"] == len(fake.requests) == 9  # 7 P&IDs and 2 PFDs

    result = conn.execute("select result from llm_extraction where source = 'pid' and source_key = %s",
                          (SHEET,)).fetchone()["result"]
    assert len(result["mentions"]) == 36
    issues = {f["issue"] for f in result["findings"] if f["kind"] not in ("connection", "title_block")}
    assert issues == {"not a tag", "not in register"} and result["lines"][0]["text"] == '6"-P-44479-A1'
    # Positions come back in sheet pixels, close to where the generator drew each tag.
    drawn = {x["key"]: x for x in truth[SHEET]["tags"]}
    for m in result["mentions"]:
        assert abs(m["x"] - drawn[m["key"]]["x"]) <= 2 and abs(m["y"] - drawn[m["key"]]["y"]) <= 2
    assert result["size"] == truth[SHEET]["size"]

    s = llm_pids.score(conn, {SHEET: truth[SHEET]})
    assert s["recall"] == round(36 / 38, 3) and s["precision"] == 1.0
    assert set(s["problems"][0]["missed"]) == {"20-TT-2200B", "20-VT-2200B"}
    assert s["piping"] == {"recall": 1.0, "precision": 1.0}


def test_drawing_endpoint_places_readings(setup, read):
    conn, truth = setup
    from fuzer.api import app
    client = TestClient(app)
    fid = conn.execute("select fuzer_id from crosswalk where source = 'pid' and source_key = %s", (SHEET,)).fetchone()["fuzer_id"]
    d = client.get(f"/documents/{fid}/drawing").json()
    assert [d["width"], d["height"]] == truth[SHEET]["size"] and d["format"] == "image" and d["read_by"]
    status = {m["status"] for m in d["marks"]}
    assert status == {"linked", "not_a_tag", "not_in_register", "line"}
    linked = [m for m in d["marks"] if m["status"] == "linked"]
    assert all(m["id"] and "x" in m for m in linked)
    assert d["connections"] and all(c["from_id"] and c["to_id"] for c in d["connections"])
    # train B's line from the inlet separator: both sheets show it, and the viewer can open the other sheet
    [end] = d["offpage"]
    assert end["status"] == "confirmed" and end["sheet"] == "OKAN-20-PID-003" and end["sheet_id"]
    assert d["loops"] and all(len(lp["members"]) >= 2 for lp in d["loops"])

    smart = conn.execute("select c.fuzer_id from crosswalk c join source_record r using (source, record_type, source_key) "
                         "where c.source = 'pid' and r.payload->>'format' = 'smart' limit 1").fetchone()["fuzer_id"]
    s = client.get(f"/documents/{smart}/drawing").json()
    assert s["read_by"] == "smart file" and s["marks"] and all("x" in m for m in s["marks"])


def test_drawings_for_an_entity(setup, read):
    conn, _ = setup
    from fuzer.api import app
    from fuzer.graph import queries
    client = TestClient(app)
    sheet = conn.execute("select fuzer_id from crosswalk where source = 'pid' and source_key = %s", (SHEET,)).fetchone()["fuzer_id"]
    tag = queries.resolve_ref(conn, PACK, "20-K-2200B")
    d = client.get(f"/entities/{tag}/drawings").json()
    assert sheet in [x["id"] for x in d["drawings"]] and d["highlight"] == [tag]
    marks = client.get(f"/documents/{sheet}/drawing").json()["marks"]
    assert any(m.get("id") == tag and "x" in m for m in marks)  # the mark the page rings
    # equipment: the sheets showing the tag it's installed at
    eq = conn.execute("select src from edge where dst = %s and rel = 'installed_at' and valid_to is null", (tag,)).fetchone()
    if eq:
        e = client.get(f"/entities/{eq['src']}/drawings").json()
        assert tag in e["highlight"] and sheet in [x["id"] for x in e["drawings"]]


def test_cached_until_the_scan_changes(setup, read):
    conn, truth = setup
    fake = FakeVision(truth)
    assert llm_pids.run(conn, PACK, client=fake)["sheets"] == 0 and fake.requests == []
    truth = {SHEET: truth[SHEET]}
    sha = conn.execute("select payload->>'image_sha256' as s from source_record where source_key = %s", (SHEET,)).fetchone()["s"]
    rescan = "update source_record set payload = jsonb_set(payload, '{image_sha256}', to_jsonb(%s::text)) where source_key = %s"
    conn.execute(rescan, ("rescanned", SHEET))
    conn.commit()
    try:
        pipeline.rebuild(conn, PACK)  # stale reading no longer applied
        assert llm_pids.score(conn, truth)["sheets_read"] == 0
    finally:  # the scan back as it was, so the tests that read the sheet still find its reading
        conn.execute(rescan, (sha, SHEET))
        conn.commit()
        pipeline.rebuild(conn, PACK)


def test_image_endpoint(setup):
    conn, _ = setup
    from fuzer.api import app
    fid = conn.execute("select fuzer_id from crosswalk where source = 'pid' and source_key = %s", (SHEET,)).fetchone()["fuzer_id"]
    r = TestClient(app).get(f"/documents/{fid}/image")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"


def test_lines_cross_sheets_where_both_drawings_agree(setup, read):
    """Off-page ends both sheets show become piping links; one only one sheet shows becomes a review question."""
    conn, _ = setup
    pipes = {(r["a"], r["b"]): r["props"] for r in conn.execute(
        """
        select la.label as a, lb.label as b, e.props from edge e
        join entity_label la on la.fuzer_id = e.src join entity_label lb on lb.fuzer_id = e.dst
        where e.rel = 'pipes_to' and e.props ? 'offpage'
        """)}
    assert ("20-V-2001", "20-V-2101A") in pipes and ("20-V-2001", "20-V-2101B") in pipes
    assert ("10-V-2004", "10-P-1001A") in pipes  # a smart sheet confirms its end too
    assert ("20-V-2001", "20-V-2101C") not in pipes
    q = conn.execute("select payload from review_item where kind = 'pid_connection' and status = 'open'").fetchall()
    assert [(r["payload"]["from"], r["payload"]["to"]) for r in q] == [("20-V-2001", "20-V-2101C")]
    # loops are kept with the reading; Okan's controllers and control valves aren't in the register
    loops = conn.execute("select result->'loops' as l from llm_extraction where source_key = %s", (SHEET,)).fetchone()
    assert loops["l"] and all(len(lp["members"]) >= 2 for lp in loops["l"])

    # a reviewer who checks both sheets and accepts links the two items
    from fuzer.api import app, deps
    item = conn.execute("select id from review_item where kind = 'pid_connection' and status = 'open'").fetchone()
    conn.commit()
    r = TestClient(app).post(f"/review/{item['id']}", json={"decision": "accept", "by": "tester"})
    assert r.status_code == 200, r.text
    deps.rebuilder.wait()
    conn.rollback()
    assert conn.execute(
        """
        select 1 from edge e join entity_label la on la.fuzer_id = e.src join entity_label lb on lb.fuzer_id = e.dst
        where e.rel = 'pipes_to' and la.label = '20-V-2001' and lb.label = '20-V-2101C' and 'review' = any(e.sources)
        """).fetchone()


def test_benchmark_scores_each_element_and_level(setup, read):
    conn, truth = setup
    s = llm_pids.score(conn, truth)
    assert {"tags", "connections", "offpage", "loops", "line_numbers"} <= set(s["elements"])
    assert s["elements"]["loops"]["recall"] == 1.0 and s["elements"]["connections"]["recall"] == 1.0
    assert s["elements"]["offpage"]["recall"] < 1.0  # train C's end was missed
    assert {"scan", "old", "poor"} <= set(s["by_level"])
    # as an eval: the pipeline behind a good reader keeps every element over its floor, and a drop is caught
    assert llm_pids.gate(s) == [], s["elements"]
    worse = {"elements": {**s["elements"], "loops": {**s["elements"]["loops"], "recall": 0.9}}}
    assert llm_pids.gate(worse, s) == ["loops recall fell from 1.0 to 0.9"]


def test_tables_and_notes_are_read_and_checked(setup, read, okan_db):
    """The alarm and trip tables land as design limits on each instrument's point, checked against the DCS and AF:
    an alarm an MOC moved without the drawing following, or never configured, is found (#475). Design ratings
    land as measurements, notes are kept with the tags they name, and the viewer gets all three."""
    conn, truth = setup
    from fuzer.analysis import limits

    design = conn.execute("select count(*) as n from point_limit where basis = 'design'").fetchone()["n"]
    assert design > 100
    # the drawing's limits never count: the alarms that count still come from the DCS and AF
    assert not conn.execute("select 1 from point_limit where basis = 'design' and source <> 'pid'").fetchone()
    found = {i["point"] for i in limits.issues(conn, None) if i["fault"] == "drawing"}
    planted = json.loads((okan_db.data / "answer_keys" / "limits.json").read_text())
    moved = {t["point"] for t in planted if t["fault"] in ("disagree", "missing") and t["point"]}
    assert found and found & moved
    m = conn.execute("select quantity, value, unit, method from measurement m join entity_label l using (fuzer_id) "
                     "where l.label = '20-V-2101B' and m.method = 'pid_table' order by quantity").fetchall()
    assert [r["quantity"] for r in m] == ["design_pressure", "design_temperature"] and m[0]["unit"] == "barg"

    from fuzer.api import app
    fid = conn.execute("select fuzer_id from crosswalk where source = 'pid' and source_key = %s", (SHEET,)).fetchone()
    d = TestClient(app).get(f"/documents/{fid['fuzer_id']}/drawing").json()
    assert {t["title"] for t in d["tables"]} == {"EQUIPMENT DATA", "ALARM AND TRIP SETTINGS"}
    psv = next(n for n in d["notes"] if n["text"].startswith("PSV-"))
    assert psv["tags"] and psv["tags"][0].startswith("20-V-")
    assert isinstance(d["checks"], list)
    s = llm_pids.score(conn, truth)
    assert s["elements"]["setpoints"]["recall"] == 1.0 and s["elements"]["notes"]["recall"] == 1.0


def test_pfds_are_read_with_their_trains_and_streams(setup, read):
    """A PFD's typical-of-3 items expand to each train's tag, its streams become piping marked with the stream
    number, and the heat and material balance lands as each stream's design case on the item it leaves (#475)."""
    conn, truth = setup
    r = conn.execute("select result from llm_extraction where source_key = 'OKAN-20-PFD-002'").fetchone()["result"]
    assert {"20-K-2100A", "20-K-2100B", "20-K-2100C"} <= {m["key"] for m in r["mentions"]}
    assert {("20-V-2001", "20-V-2101C"), ("20-K-2100B", "20-E-2150B")} <= {(c["from"], c["to"]) for c in r["connections"]}
    assert not {("20-K-2100A", "20-E-2150B")} & {(c["from"], c["to"]) for c in r["connections"]}  # trains stay apart
    assert not [f for f in r["findings"] if f["kind"] == "connection"]  # battery limits aren't findings
    pfd_pipes = conn.execute("select count(*) as n from edge where rel = 'pipes_to' and props ? 'stream'").fetchone()
    assert pfd_pipes["n"] >= 27
    rows = conn.execute(
        """select m.quantity, m.value, m.unit, m.location from measurement m join entity_label l using (fuzer_id)
           where l.label = '20-K-2100B' and m.method = 'pfd_design' order by quantity""").fetchall()
    st4 = next(s for s in truth["OKAN-20-PFD-002"]["streams"] if s["from"] == "20-K-2100A/B/C")
    assert {r["quantity"] for r in rows} == {"design_case_pressure", "design_case_temperature", "design_case_flow"}
    assert all(r["location"] == f"stream {st4['n']}" for r in rows)
    flow = next(r for r in rows if r["quantity"] == "design_case_flow")
    assert f"{flow['value']:g} {flow['unit']}" == st4["flow"]
    s = llm_pids.score(conn, truth)
    assert s["elements"]["streams"]["recall"] == 1.0


def test_an_instrument_the_register_puts_elsewhere_is_flagged(setup, read):
    """Train B's 2nd stage suction pressure transmitter was moved to the compressor in the field (clouded on the
    drawing); SAP still has it under the scrubber. The drawing view says so, and nothing else on the sheet (#475)."""
    conn, truth = setup
    drawn = {t["key"]: t for t in truth[SHEET]["tags"]}
    assert drawn["20-PT-2201B"]["on"] == "20-K-2200B"
    from fuzer.api import app
    fid = conn.execute("select fuzer_id from crosswalk where source = 'pid' and source_key = %s", (SHEET,)).fetchone()
    checks = TestClient(app).get(f"/documents/{fid['fuzer_id']}/drawing").json()["checks"]
    placed = [c for c in checks if c["type"] == "placement"]
    assert [c["tag"] for c in placed] == ["20-PT-2201B"]
    assert "20-K-2200B" in placed[0]["detail"] and "20-V-2201B" in placed[0]["detail"]


def test_drawing_disagreements_are_review_items(setup, read):
    """Each place a drawing and the DCS, AF or SAP disagree is a question in Data review (#475): the relocated PT
    SAP still files under the scrubber, and alarm and trip settings that differ from (or are missing in) the
    configured alarms. A reviewer says which side needs changing; the decision changes neither side and is kept."""
    conn, _ = setup
    items = {r["subject"]: r["payload"] for r in conn.execute(
        "select subject, payload from review_item where kind = 'pid_mismatch' and status = 'open'")}
    placed = [p for p in items.values() if p["check"] == "placement"]
    assert [(p["sheet"], p["tag"], p["drawn_on"]) for p in placed] == [(SHEET, "20-PT-2201B", "20-K-2200B")]
    assert placed[0]["document"] and placed[0]["fuzer_id"] and "SAP" in placed[0]["question"].upper()
    alarms = [p for p in items.values() if p["check"] in ("alarm", "alarm_unset")]
    from fuzer.analysis import limits
    drawn = {i["point_id"] for i in limits.issues(conn, None) if i["fault"] == "drawing"}
    assert alarms and {p["fuzer_id"] for p in alarms} == drawn
    assert all(p["sheet"] and p["tag"] and p["detail"] in p["question"] for p in alarms)

    from fuzer.api import app
    client = TestClient(app)
    rows = client.get("/review", params={"kind": "pid_mismatch"}).json()
    assert len(rows) == len(items) and all(r["label"] for r in rows)  # the point or instrument it's about
    item = next(r for r in rows if r["payload"]["check"] == "placement")
    conn.commit()
    assert client.post(f"/review/{item['id']}", json={"decision": "accept", "by": "tester"}).status_code == 400
    assert client.post(f"/review/{item['id']}", json={"decision": "choose", "value": "x", "by": "tester"}).status_code == 400
    r = client.post(f"/review/{item['id']}", json={"decision": "choose", "value": "drawing", "by": "tester"})
    assert r.status_code == 200 and "rebuild" not in r.json(), r.text
    conn.rollback()
    # a rebuild raises the same disagreement again: the decision stands, nothing reopens, SAP is untouched
    pipeline.rebuild(conn, PACK)
    row = conn.execute("select status, decision from review_item where id = %s", (item["id"],)).fetchone()
    assert row["status"] == "decided" and row["decision"]["value"] == "drawing"
    assert not conn.execute("select 1 from review_item where kind = 'pid_mismatch' and status = 'open' "
                            "and payload->>'check' = 'placement'").fetchone()
