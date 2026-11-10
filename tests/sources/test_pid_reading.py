"""Reading P&IDs without a database (#475): line numbers in parts, denser tiles where the ink is, and a
second look at readings Claude was unsure of."""

import json
from types import SimpleNamespace

from PIL import Image, ImageDraw

from fuzer.sources import llm_pids


def test_line_numbers_parse_into_parts():
    assert llm_pids.parse_line('6"-P-20012-A1A-IH') == {"size_in": 6.0, "service": "P", "number": "20012",
                                                        "spec": "A1A", "insulation": "IH"}
    assert llm_pids.parse_line('3/4"-D-202400-A1A')["size_in"] == 0.75
    assert llm_pids.parse_line('12"-P-20951-A1A')["insulation"] is None
    assert llm_pids.parse_line("20-PT-2001") is None and llm_pids.parse_line("") is None


def test_dense_tiles_are_split():
    img = Image.new("L", (2800, 1400), 255)
    ImageDraw.Draw(img).rectangle([0, 0, 1300, 1400], fill=0)  # the left half is solid ink
    plain, adaptive = llm_pids.tile_boxes(2800, 1400), llm_pids.tile_boxes(2800, 1400, img)
    assert len(plain) == 2 and len(adaptive) == 5
    assert adaptive[-1] == plain[1]  # the empty half is left alone


def test_unsure_readings_get_a_second_look(tmp_path):
    path = tmp_path / "sheet.png"
    Image.new("L", (1400, 1000), 255).save(path)
    boxes = llm_pids.tile_boxes(1400, 1000)
    data = {"tags": [{"text": "PT-2OO1", "kind": "instrument", "confidence": 0.4, "tile": 1, "x": 300, "y": 300},
                     {"text": "LT-2001", "kind": "instrument", "confidence": 0.95, "tile": 1, "x": 600, "y": 300},
                     {"text": "TT-9", "kind": "instrument", "confidence": 0.5, "tile": 1, "x": 900, "y": 300}]}
    sent = []

    def create(**kw):
        sent.append(kw)
        answer = {"readings": [{"crop": 1, "text": "PT-2001", "confidence": 0.9},
                               {"crop": 2, "text": "TT-8", "confidence": 0.3}]}  # less sure: the first reading stays
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(answer))],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0))

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    out, message = llm_pids.reread(client, "m", "low", str(path), data, boxes)
    assert message is not None and sum(1 for b in sent[0]["messages"][0]["content"] if b["type"] == "image") == 2
    assert out["tags"][0]["text"] == "PT-2001" and out["tags"][0]["first_read"] == "PT-2OO1"
    assert out["tags"][2]["text"] == "TT-9" and "first_read" not in out["tags"][2]
    sure = {"tags": [data["tags"][1]]}
    assert llm_pids.reread(client, "m", "low", str(path), sure, boxes) == (sure, None) and len(sent) == 1
