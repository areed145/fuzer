"""Claude reads P&ID drawings (scans and clean exports): tags, piping and where each tag sits.

Each drawing is cut into overlapping tiles (small text stays readable after the model's image
resizing) and sent in one request. Claude transcribes every tag as drawn; Fuzer then turns each
reading into a canonical key with the asset pack's tag grammar (area from the sheet) and checks it
against the register. Readings that are not tags, or tags missing from the register, are kept as
findings instead of becoming links. Claude is deliberately not shown the expected tags, so it
can't "find" ones that aren't on the drawing.

Beyond tags, Claude reads the topology (#475): each process line between two items with the valves it runs
through, line numbers (parsed into size, service, number, spec and insulation), off-page connectors (a line
leaving for another sheet, with the tag at the other end) and instrument loops (transmitter, controller,
final element). Off-page ends are joined across sheets: a line both sheets agree on becomes a `pipes_to`
link; one only one side shows becomes a review item, not a link. Loops whose transmitter and final element
are both registered become `controls` links.

Claude also reads the sheet's data tables and numbered notes (#475): an equipment data table becomes design
pressure and temperature measurements, an alarm and trip table becomes `design` limits on each instrument's point
(checked against the DCS and AF by the limits checks, never the limits that count), and each note keeps the
registered tags it names.

Dense regions get smaller tiles (a tile with a lot of ink is split in four), and readings Claude is unsure
of get a second look: each is cropped at full resolution, enlarged, and read again on its own.

Every reading carries the tile it was read on and a point within that tile, turned into sheet
pixels so the UI can mark it on the drawing. Smart P&IDs are not sent: their tags and piping are
exact already. Results are cached in llm_extraction per sheet, keyed by the image's SHA-256, and
applied during rebuilds without calling the model.
"""

import base64
import io
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image
from psycopg.types.json import Jsonb

from ..core import llm, nel
from ..core.llm import call, run_batch, usage
from ..core.model import Edge, Limit, Measurement
from ..core.pack import Pack

PROMPT_VERSION = "2026-11-11.2"
TILE_TARGET = 1400  # source pixels per tile side before overlap
MAX_EDGE = 1568  # long edge sent per tile
OVERLAP = 0.10
DENSE = 0.08  # share of dark pixels above which a tile is split in four
LOW = 0.6  # readings below this confidence get a second, closer look
REREAD_MAX = 12
CROP = 320  # source pixels around a reading for the second look

SYSTEM_PROMPT = """\
You transcribe tag numbers from scanned piping and instrumentation diagrams (P&IDs) of an oil and gas
facility. The drawing comes as overlapping tiles in reading order.

Report every tag you can read:
- instrument bubbles: function letters on the top line, loop number with any suffix letter on the
  bottom line. Write them FUNCTION-LOOP, e.g. PT-1001A. kind "instrument". In `on`, give the tag of the
  equipment its process connection (the line from the bubble down to the process) runs to, as written, or ""
  when it sits on a line or you can't tell. Give "" in `on` for everything that isn't an instrument.
- equipment tags written by vessels, pumps, compressors and exchangers, e.g. 44-P-1001A. kind "equipment".
- line numbers on process lines, e.g. 6"-P-44123-A1. kind "line".
- anything else that looks like an identifier: kind "other".

Also report the topology:
- connections: each process line between two pieces of equipment, as from / to (the tags at each end,
  following the flow arrow), the line number written on it, and the valves it runs through in flow order
  in `via` (a valve's tag if it has one, such as LV-2001, else its type: gate, globe, ball, check,
  butterfly, strainer, orifice, reducer, spectacle_blind; drains, vents and sample points branch off a line and
  aren't in `via`). A line that only crosses another (drawn with or without a jump) is not connected to it.
- offpage: each off-page connector (an arrow-shaped box at the sheet edge reading TO or FROM another
  drawing number): direction "to" or "from", the drawing number, the tag on this sheet the line comes
  from or goes to, the tag written in the connector (the item on the other sheet), and the line number.
- loops: each instrument loop joined by signal lines (dashed, or dashed with small circles for software
  links): its members in order, transmitter, controller, final element, e.g. LT-2001, LIC-2001, LV-2001.

Write each tag exactly as drawn and don't add an area or prefix that isn't written. Report a tag once
even if it appears on several tiles, giving the tile number where it is clearest and the centre of
the label within that tile as x and y in that tile's pixels (each tile's size is given; 0, 0 is its
top-left corner). If a character is
unclear, give your best reading and a lower confidence (0 to 1). Also read the title block, with its date
if one is written.

Some sheets are process flow diagrams (PFDs): main equipment and the process streams between them, each stream
with its number in a diamond. Give a stream's number as its connection's `stream` (and "" for `line` when no
line number is written); a stream from or to the edge of the unit has the label written there (e.g.
"FROM JACKETS") as its other end. An item drawn once for several identical trains is tagged like
20-K-2100A/B/C: write that tag exactly as drawn. On a P&ID, give "" for `stream`.

Read the sheet's data tables and notes too:
- tables: each ruled table (equipment data, alarm and trip settings, line lists, a PFD's heat and material
  balance with one column per stream, ...): its title, its column
  headings as written, and every row's cells as written, one string per column ("-" where a cell is blank or
  a dash). Copy numbers exactly; don't convert units.
- notes: each numbered note, with its number and its text as written."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title_block", "tags", "connections", "offpage", "loops", "tables", "notes"],
    "properties": {
        "title_block": {
            "type": "object",
            "additionalProperties": False,
            "required": ["drawing_number", "title", "revision", "date"],
            "properties": {"drawing_number": {"type": "string"}, "title": {"type": "string"},
                           "revision": {"type": "string"}, "date": {"type": "string"}},
        },
        "tables": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "columns", "rows"],
            "properties": {"title": {"type": "string"}, "columns": {"type": "array", "items": {"type": "string"}},
                           "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}},
        }},
        "notes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["number", "text"],
            "properties": {"number": {"type": "integer"}, "text": {"type": "string"}},
        }},
        "connections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["from", "to", "line", "via", "stream"],
            "properties": {"from": {"type": "string"}, "to": {"type": "string"}, "line": {"type": "string"},
                           "via": {"type": "array", "items": {"type": "string"}}, "stream": {"type": "string"}},
        }},
        "offpage": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["direction", "sheet", "tag", "other", "line"],
            "properties": {"direction": {"type": "string", "enum": ["to", "from"]}, "sheet": {"type": "string"},
                           "tag": {"type": "string"}, "other": {"type": "string"}, "line": {"type": "string"}},
        }},
        "loops": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["members"],
            "properties": {"members": {"type": "array", "items": {"type": "string"}}},
        }},
        "tags": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "kind", "confidence", "tile", "x", "y", "on"],
            "properties": {
                "text": {"type": "string"},
                "on": {"type": "string"},
                "kind": {"type": "string", "enum": ["instrument", "equipment", "line", "other"]},
                "confidence": {"type": "number"},
                "tile": {"type": "integer"},
                "x": {"type": "number"},
                "y": {"type": "number"},
            },
        }},
    },
}


def tile_boxes(w: int, h: int, img: Image.Image | None = None) -> list[tuple[int, int, int, int]]:
    """Overlapping tile boxes (left, top, right, bottom) covering a w x h drawing, in reading order. With the
    image, a tile dense with ink is split into four overlapping quarters, so small text there stays readable."""
    cols, rows = max(1, math.ceil(w / TILE_TARGET)), max(1, math.ceil(h / TILE_TARGET))
    tw, th = w / cols, h / rows
    boxes = [(max(0, int(c * tw - tw * OVERLAP)), max(0, int(r * th - th * OVERLAP)),
              min(w, int((c + 1) * tw + tw * OVERLAP)), min(h, int((r + 1) * th + th * OVERLAP)))
             for r in range(rows) for c in range(cols)]
    if img is None:
        return boxes
    out = []
    for b in boxes:
        if _ink(img.crop(b)) <= DENSE:
            out.append(b)
            continue
        mx, my, ox, oy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2, int((b[2] - b[0]) * OVERLAP), int((b[3] - b[1]) * OVERLAP)
        out += [(b[0], b[1], mx + ox, my + oy), (mx - ox, b[1], b[2], my + oy),
                (b[0], my - oy, mx + ox, b[3]), (mx - ox, my - oy, b[2], b[3])]
    return out


def _ink(tile: Image.Image) -> float:
    small = tile.convert("L").resize((max(1, tile.size[0] // 4), max(1, tile.size[1] // 4)))
    hist = small.histogram()
    return sum(hist[:128]) / max(1, sum(hist))


def boxes_for(path: str) -> list[tuple[int, int, int, int]]:
    """The tile boxes a drawing is cut into (adaptive to its density)."""
    img = Image.open(path).convert("L")
    return tile_boxes(*img.size, img)


def tiles(path: str) -> list[tuple[str, bytes, tuple[int, int, int, int]]]:
    """Overlapping PNG tiles of a drawing, each no larger than MAX_EDGE on its long side, with the
    box (in sheet pixels) each was cut from."""
    img = Image.open(path).convert("L")
    boxes = tile_boxes(*img.size, img)
    out = []
    for i, box in enumerate(boxes):
        tile = img.crop(box)
        if max(tile.size) > MAX_EDGE:
            tile.thumbnail((MAX_EDGE, MAX_EDGE))
        buf = io.BytesIO()
        tile.save(buf, format="PNG", optimize=True)
        label = (f"{i + 1} of {len(boxes)} (sheet pixels {box[0]}-{box[2]} across, {box[1]}-{box[3]} down; "
                 f"{tile.size[0]} x {tile.size[1]} px as sent)")
        out.append((label, buf.getvalue(), box))
    return out


LINE_NO = re.compile(r"""^\s*(?P<size>\d+(?:\.\d+)?(?:\s*/\s*\d+)?)\s*(?:"|IN|''|”)?\s*-\s*(?P<service>[A-Z]{1,4})\s*-\s*
                     (?P<number>[A-Z0-9]{3,8})\s*-\s*(?P<spec>[A-Z0-9]{1,5})(?:\s*-\s*(?P<insulation>[A-Z]{1,3}))?\s*$""",
                     re.VERBOSE | re.IGNORECASE)


def parse_line(text: str) -> dict | None:
    """A line number in parts: 6"-P-20012-A1A-IH -> size 6 (inches), service P, number 20012, spec A1A,
    insulation IH. None when it isn't one."""
    m = LINE_NO.match(text or "")
    if not m:
        return None
    size = m["size"].replace(" ", "")
    if "/" in size:
        a, b = size.split("/")
        inches = float(a) / float(b)
    else:
        inches = float(size)
    return {"size_in": inches, "service": m["service"].upper(), "number": m["number"].upper(),
            "spec": m["spec"].upper(), "insulation": (m["insulation"] or "").upper() or None}


def _norm_line(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper().replace("”", '"'))


def _request(model: str, effort: str, sheet: dict) -> dict:
    content = [{"type": "text", "text": f"Drawing {sheet['sheet']}. Register title: {sheet.get('title', '')}."}]
    for label, png, _ in tiles(sheet["image_path"]):
        content.append({"type": "text", "text": f"Tile {label}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.standard_b64encode(png).decode()}})
    return {
        "model": model,
        "max_tokens": 16000,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
    }


def _area(sheet: dict, pack: Pack) -> str | None:
    return pack.tags.area_in(sheet.get("title", ""))


def sheet_xy(t: dict, boxes) -> dict:
    """A reading's point in its tile (pixels as sent) as sheet pixels; {} without a usable tile."""
    if not boxes or not isinstance(t.get("tile"), int) or not 1 <= t["tile"] <= len(boxes):
        return {}
    x0, y0, x1, y1 = boxes[t["tile"] - 1]
    scale = min(1.0, MAX_EDGE / max(x1 - x0, y1 - y0))  # tiles larger than MAX_EDGE were shrunk before sending
    x = min(max(t.get("x", 0) / scale, 0), x1 - x0)
    y = min(max(t.get("y", 0) / scale, 0), y1 - y0)
    return {"x": round(x0 + x), "y": round(y0 + y)}


REREAD_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["readings"],
    "properties": {"readings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["crop", "text", "confidence"],
        "properties": {"crop": {"type": "integer"}, "text": {"type": "string"}, "confidence": {"type": "number"}}}}},
}


def _data(message) -> dict:
    if message.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("answer cut off at max_tokens")
    return json.loads(next(b.text for b in message.content if b.type == "text"))


def reread(client, model: str, effort: str, path: str, data: dict, boxes) -> tuple[dict, object | None]:
    """The second look: readings below LOW confidence, each cropped at full resolution around where it was read,
    enlarged and read again on its own. A rereading replaces the first only when it is surer. Returns the data and
    the extra message (None when nothing needed a second look)."""
    low = [t for t in data["tags"] if t["confidence"] < LOW and t["kind"] != "line" and sheet_xy(t, boxes)]
    low = low[:REREAD_MAX]
    if not low:
        return data, None
    img = Image.open(path).convert("L")
    content = [{"type": "text", "text": "Each crop is centred on one label from a P&ID that was hard to read at first. "
                                        "Read the label at the centre of each crop exactly as written, with your "
                                        "confidence (0 to 1)."}]
    for i, t in enumerate(low, 1):
        p = sheet_xy(t, boxes)
        crop = img.crop((p["x"] - CROP // 2, p["y"] - CROP // 2, p["x"] + CROP // 2, p["y"] + CROP // 2))
        crop = crop.resize((CROP * 2, CROP * 2), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        crop.save(buf, format="PNG", optimize=True)
        content += [{"type": "text", "text": f"Crop {i} (first read as {t['text']!r}):"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": base64.standard_b64encode(buf.getvalue()).decode()}}]
    message = call(client, {"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": content}],
                            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": REREAD_SCHEMA}}})
    for r in _data(message)["readings"]:
        if 1 <= r["crop"] <= len(low) and r["confidence"] > low[r["crop"] - 1]["confidence"]:
            t = low[r["crop"] - 1]
            t.update(first_read=t["text"], text=r["text"], confidence=r["confidence"])
    return data, message


# Data tables and notes (#475): an alarm and trip table has two or more of these columns; an equipment data
# table has a design pressure column.
LIMIT_COLS = {"LL": "lolo", "LOLO": "lolo", "LALL": "lolo", "L": "lo", "LO": "lo", "LAL": "lo", "H": "hi", "HI": "hi",
              "LAH": "hi", "HH": "hihi", "HIHI": "hihi", "LAHH": "hihi"}
NUMBER = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)")


def _number(cell) -> float | None:
    m = NUMBER.match(str(cell or "").replace(",", ""))
    return float(m[1]) if m else None


UNIT_WORDS = {"DEGC": "degC", "°C": "degC", "DEGF": "degF", "°F": "degF"}


def _unit_in(heading: str) -> str | None:
    m = re.search(r"\(([^)]+)\)", heading)
    if not m:
        return None
    unit = m[1].strip()
    return UNIT_WORDS.get(unit.upper().replace(" ", ""), unit)


def read_foot(tables: list[dict], notes: list[dict], area: str | None, pack: Pack, known: set[str]) -> dict:
    """A sheet's data tables and notes, read: alarm and trip settings per instrument (`setpoints`), design ratings
    per item (`equipment_data`) and notes with the registered tags they name. Table rows are lists of cells, or
    {cells} as a smart sheet exports them. Rows whose tag isn't in the register become findings."""
    setpoints, equipment, findings, streams = [], [], [], []
    for t in tables:
        raw = [re.sub(r"\s+", " ", str(c)).strip() for c in t.get("columns", [])]
        cols = [c.upper() for c in raw]
        heads = [re.sub(r"\(.*?\)", "", c).replace(".", "").replace(" ", "") for c in cols]
        kinds = [LIMIT_COLS.get(h) for h in heads]
        rows = [r["cells"] if isinstance(r, dict) else r for r in t.get("rows", [])]

        def col(pred, cols=cols):
            return next((i for i, c in enumerate(cols) if pred(c)), None)

        def tag_of(r, t=t):
            key = pack.tags.parse(str(r[0]), default_area=area) if r else None
            if key in known:
                return key
            if r and any(_number(c) is not None for c in r[1:]):
                findings.append({"text": str(r[0]), "kind": "table_row", "table": t.get("title"),
                                 **({"key": key} if key else {}), "issue": "not in register" if key else "not a tag"})
            return None

        if cols and cols[0].startswith("STREAM"):  # a heat and material balance: one column per stream
            for j, n in enumerate(raw[1:], 1):
                st = {"n": n.strip()}
                for r in rows:
                    label = str(r[0]).upper() if r else ""
                    cell = str(r[j]).strip() if j < len(r) else ""
                    for word, k in (("PRESS", "pressure"), ("TEMP", "temperature"), ("FLOW", "flow"),
                                    ("DESC", "description"), ("PHASE", "phase")):
                        if word in label:
                            if k in ("pressure", "temperature"):
                                st[k] = _number(cell)
                                st[k + "_unit"] = _unit_in(str(r[0])) or ("barg" if k == "pressure" else "degC")
                            elif k == "flow":
                                st["flow"] = _number(cell)
                                st["flow_unit"] = re.sub(r"^[-+\d.,\s]+", "", cell).strip() or None
                            else:
                                st[k] = cell if cell not in ("", "-") else None
                streams.append(st)
            continue
        if sum(1 for k in kinds if k) >= 2:
            iu = col(lambda c: c.startswith("UNIT"))
            for r in rows:
                if not (key := tag_of(r)):
                    continue
                lim = {kinds[i]: v for i in range(1, min(len(r), len(kinds)))
                       if kinds[i] and (v := _number(r[i])) is not None}
                if lim:
                    unit = str(r[iu]).strip() if iu is not None and iu < len(r) else ""
                    setpoints.append({"tag": key, "text": str(r[0]), "limits": lim, "unit": unit or None})
        elif (ip := col(lambda c: "PRESS" in c or re.search(r"\bP\b", re.sub(r"\(.*?\)", "", c)))) is not None:
            it = col(lambda c: "TEMP" in c or re.search(r"\bT\b", re.sub(r"\(.*?\)", "", c)))
            ic, isv = col(lambda c: c.startswith("CAPAC")), col(lambda c: c.startswith("SERV"))
            for r in rows:
                if not (key := tag_of(r)):
                    continue

                def cell(row, i):
                    return str(row[i]).strip() if i is not None and i < len(row) else ""

                row = {"tag": key, "text": str(r[0]), "service": cell(r, isv) or None,
                       "design_pressure": _number(cell(r, ip)), "pressure_unit": _unit_in(raw[ip]) or "barg",
                       "design_temperature": _number(cell(r, it)) if it is not None else None,
                       "temperature_unit": (_unit_in(raw[it]) if it is not None else None) or "degC",
                       "capacity": cell(r, ic) if cell(r, ic) not in ("", "-") else None}
                equipment.append(row)
    read_notes = []
    for n in notes:
        keys = sorted({m.key for m in nel.find_mentions(n["text"], pack, default_area=area) if m.key in known})
        read_notes.append({"n": n["n"], "text": n["text"], "tags": keys})
    return {"setpoints": setpoints, "equipment_data": equipment, "notes": read_notes, "streams": streams,
            "findings": findings}


def typicals(text: str) -> list[str]:
    """A tag drawn once for identical trains, 20-K-2100A/B/C, as the tags it stands for; any other text as itself."""
    m = re.fullmatch(r"\s*(\S*?)([A-Z])((?:\s*/\s*[A-Z])+)\s*", text or "")
    if not m:
        return [text]
    return [m[1] + m[2]] + [m[1] + x.strip() for x in m[3].split("/")[1:]]


def interpret(data: dict, sheet: dict, pack: Pack, known_tags: set[str],
              boxes: list[tuple[int, int, int, int]] | None = None) -> dict:
    """Claude's readings -> links to known tags, plus findings for everything else. With the tile
    boxes, each reading's position in its tile (pixels as sent) becomes a position on the sheet."""
    area = _area(sheet, pack)
    mentions, findings, lines, seen = [], [], [], set()

    def at(t):
        return sheet_xy(t, boxes)

    tags = [{**t, "text": x, **({"drawn": t["text"]} if x != t["text"] else {})}
            for t in data["tags"] for x in (typicals(t["text"]) if t["kind"] != "line" else [t["text"]])]
    for t in tags:
        if t["kind"] == "line":
            lines.append({"text": t["text"], **at(t)})
            continue
        key = pack.tags.parse(t["text"], default_area=area)
        if key is None:
            findings.append({"text": t["text"], "kind": t["kind"], "issue": "not a tag", **at(t)})
        elif key not in known_tags:
            findings.append({"text": t["text"], "kind": t["kind"], "key": key, "issue": "not in register", **at(t)})
        elif key not in seen:
            seen.add(key)
            on = pack.tags.parse(t.get("on") or "", default_area=area) if t["kind"] == "instrument" else None
            mentions.append({"text": t["text"], "kind": "tag", "key": key, "confidence": t["confidence"], **at(t),
                             **({"on": on} if on in known_tags and on != key else {})})
    def key(text):
        return pack.tags.parse(text or "", default_area=area)

    connections = []
    for c in data.get("connections", []):
        froms, tos = typicals(c["from"]), typicals(c["to"])
        if len(froms) != len(tos) and len(froms) > 1 and len(tos) > 1:
            pairs = []  # trains that don't line up: nothing safe to link
        elif len(froms) == len(tos):
            pairs = list(zip(froms, tos, strict=True))  # train to train
        else:
            pairs = [(f, t) for f in froms for t in tos]  # a header into (or out of) every train
        for fa, tb_ in pairs:
            a, b = key(fa), key(tb_)
            if a in known_tags and b in known_tags and a != b:
                connections.append({"from": a, "to": b, "line": c["line"] or None, "via": c.get("via", []),
                                    **({"stream": c["stream"]} if c.get("stream") else {}),
                                    **({"line_parts": lp} if (lp := parse_line(c["line"])) else {})})
            elif not (c.get("stream") and (a is None or b is None)):  # a stream to a battery limit isn't one
                findings.append({"text": f"{fa} -> {tb_}", "kind": "connection", "issue": "end not in register"})
    offpage = []
    for o in data.get("offpage", []):
        here, there = key(o["tag"]), pack.tags.parse(o["other"] or "", default_area=pack.tags.area_in(o["sheet"] or ""))
        offpage.append({"direction": o["direction"], "sheet": (o["sheet"] or "").replace(" ", "").upper(),
                        "tag": here, "other": there, "line": o["line"], "text": o["tag"],
                        "registered": here in known_tags and there in known_tags})
    loops = []
    for lp in data.get("loops", []):
        members = [key(m) or m for m in lp["members"]]
        loops.append({"members": members, "registered": [m in known_tags for m in members]})
    tb = data["title_block"]
    if tb["drawing_number"] and tb["drawing_number"].replace(" ", "") != sheet["sheet"]:
        findings.append({"text": tb["drawing_number"], "kind": "title_block", "issue": "drawing number differs from register"})
    if tb["revision"] and sheet.get("revision") and tb["revision"].strip().upper() != str(sheet["revision"]).upper():
        findings.append({"text": tb["revision"], "kind": "title_block", "issue": "revision differs from register"})
    for ln in lines:
        if lp := parse_line(ln["text"]):
            ln["parts"] = lp
    foot = read_foot(data.get("tables", []), [{"n": n["number"], "text": n["text"]} for n in data.get("notes", [])],
                     area, pack, known_tags)
    findings += foot.pop("findings")
    for st in foot["streams"]:  # a PFD's stream conditions belong to the item(s) each stream leaves
        st["from"] = sorted({c["from"] for c in connections if c.get("stream") == st["n"]})
    out = {"title_block": tb, "mentions": mentions, "findings": findings, "lines": lines, "connections": connections,
           "offpage": offpage, "loops": loops, "tables": data.get("tables", []), **foot}
    if boxes:
        out["size"] = [max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return out


def run(conn, pack: Pack, *, client=None, model: str | None = None, effort: str | None = None,
        limit: int | None = None, workers: int = 4, batch: bool = False, force: bool = False,
        dry_run: bool = False, poll_seconds: int = 30) -> dict:
    """Read drawings (all but smart P&IDs) that have no current cached result. Needs a rebuilt register first."""
    model, effort = model or llm.model(), effort or llm.effort()  # the settings (#131) unless given
    rows = conn.execute(
        """
        select r.source_key, r.payload, x.text_hash, x.prompt_version
        from source_record r
        left join llm_extraction x on x.source = r.source and x.source_key = r.source_key
        where r.source = 'pid' and r.record_type = 'sheet' and r.payload->>'format' <> 'smart'
          and r.payload ? 'image'
        order by r.source_key
        """
    ).fetchall()
    missing = [r["source_key"] for r in rows if not r["payload"].get("image_sha256")]
    todo = [r for r in rows if r["payload"].get("image_sha256") and (
        force or r["text_hash"] != r["payload"]["image_sha256"] or r["prompt_version"] != PROMPT_VERSION)][:limit]
    stats = {"sheets": len(todo), "model": model, "effort": effort, "provider": llm.provider(),
             "image_missing": len(missing),
             "already_cached": len(rows) - len(todo) - len(missing) if not force else 0}
    if dry_run or not todo:
        # Images cost about width x height / 750 tokens each.
        n_tiles = sum(len(tiles(r["payload"]["image_path"])) for r in todo[:5]) / max(1, min(len(todo), 5))
        stats["estimated_input_tokens"] = int(len(todo) * n_tiles * MAX_EDGE * MAX_EDGE * 0.75 / 750)
        return stats

    if client is None:
        client = llm.client()
    known_tags = {r["key"] for r in conn.execute("select key from entity_key where kind = 'tag'")}
    sheets = {r["source_key"]: r["payload"] for r in todo}
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "failed_requests": 0,
              "stored": 0, "tags_linked": 0, "findings": 0}
    errors: list[str] = []

    def handle(sk, data, messages):
        result = interpret(data, sheets[sk], pack, known_tags, boxes_for(sheets[sk]["image_path"]))
        result["reread"] = sum(1 for t in data["tags"] if "first_read" in t)
        for message in messages:
            for k, v in usage(message).items():
                totals[k] += v
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into llm_extraction (source, source_key, text_hash, model, prompt_version, result)
                values ('pid', %s, %s, %s, %s, %s)
                on conflict (source, source_key) do update
                set text_hash = excluded.text_hash, model = excluded.model, prompt_version = excluded.prompt_version,
                    result = excluded.result, created_at = now()
                """,
                (sk, sheets[sk]["image_sha256"], model, PROMPT_VERSION, Jsonb(result)),
            )
        conn.commit()
        totals["stored"] += 1
        totals["tags_linked"] += len(result["mentions"])
        totals["findings"] += len(result["findings"])

    def read(sk, p):
        first = call(client, _request(model, effort, p))
        data, second = reread(client, model, effort, p["image_path"], _data(first), boxes_for(p["image_path"]))
        return data, [first] + ([second] if second else [])

    if batch:  # batches read once: the second look needs the first answer back
        run_batch(client, {sk: _request(model, effort, p) for sk, p in sheets.items()},
                  lambda sk, m: handle(sk, _data(m), [m]), totals, errors, poll_seconds)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(read, sk, p): sk for sk, p in sheets.items()}
            for fut in as_completed(futures):
                try:
                    handle(futures[fut], *fut.result())
                except Exception as e:
                    totals["failed_requests"] += 1
                    errors.append(f"{futures[fut]}: {type(e).__name__}: {e}")
    return {**stats, **totals, **llm.cost_report(model, totals), "errors": errors[:5]}


def _readings(conn, sheets: dict[str, dict]) -> dict[str, dict]:
    """Each sheet's current reading: Claude's cached result while the image is unchanged, else nothing."""
    out = {}
    for row in conn.execute("select source_key, text_hash, model, result from llm_extraction where source = 'pid'"):
        payload = sheets.get(row["source_key"])
        if payload is not None and row["text_hash"] == payload.get("image_sha256"):
            out[row["source_key"]] = {**row["result"], "model": row["model"]}
    return out


def join(conn, sheets: dict[str, dict], readings: dict[str, dict] | None = None) -> dict:
    """Off-page ends joined across sheets. An end is confirmed when the sheet it points to (read by Claude, or a
    smart sheet's own off-page list) has the opposite end: same line number, or naming this end's tag. Returns
    {connections: [{from, to, line, sheets, confirmed}], disputed: [end ...]}: a disputed end points at a sheet
    that is known (read or smart) but doesn't show the line; ends pointing at unread sheets are neither."""
    readings = _readings(conn, sheets) if readings is None else readings
    ends: dict[str, list[dict]] = {}
    for sk, r in readings.items():
        ends[sk] = [e for e in r.get("offpage", []) if e.get("tag")]
    for sk, p in sheets.items():
        if p.get("format") == "smart" and p.get("offpage"):
            ends[sk] = [{**e, "registered": True} for e in p["offpage"]]  # a smart sheet's tags are the register's
    connections, disputed, seen = [], [], set()
    for sk, own in ends.items():
        for e in own:
            other = (e.get("sheet") or "").replace(" ", "").upper()
            there = ends.get(other)
            if there is None:
                continue  # the other sheet isn't read yet
            back = [b for b in there if b.get("direction") != e["direction"] and (
                _norm_line(b.get("line")) == _norm_line(e.get("line")) or b.get("tag") == e.get("other"))]
            a, b = (e["tag"], e.get("other")) if e["direction"] == "to" else (e.get("other"), e["tag"])
            if back:
                a, b = (e["tag"], back[0]["tag"]) if e["direction"] == "to" else (back[0]["tag"], e["tag"])
                k = (a, b, _norm_line(e.get("line")))
                if k not in seen:
                    seen.add(k)
                    connections.append({"from": a, "to": b, "line": e.get("line"), "sheets": sorted([sk, other]),
                                        "confirmed": True,
                                        "registered": bool(e.get("registered") and back[0].get("registered"))})
            else:
                disputed.append({"sheet": sk, "other_sheet": other, "direction": e["direction"], "tag": e["tag"],
                                 "other": e.get("other"), "line": e.get("line"), "from": a, "to": b,
                                 "registered": bool(e.get("registered"))})
    return {"connections": connections, "disputed": disputed}


def feet(conn, sheets: dict[str, dict], pack: Pack, readings: dict[str, dict] | None = None) -> dict[str, dict]:
    """Each sheet's data tables and notes as read (read_foot's shape, plus the sheet's `date`): from Claude's
    current reading, or from the file for a smart sheet, which exports its tables and notes."""
    readings = _readings(conn, sheets) if readings is None else readings
    known = None
    out = {}
    for sk, p in sheets.items():
        if p.get("format") == "smart" and (p.get("tables") or p.get("notes")):
            if known is None:
                known = {r["key"] for r in conn.execute("select key from entity_key where kind = 'tag'")}
            f = read_foot(p.get("tables", []), p.get("notes", []), _area(p, pack), pack, known)
            out[sk] = {**f, "streams": [], "date": p.get("date")}
        elif sk in readings and ("setpoints" in readings[sk] or "notes" in readings[sk] or "streams" in readings[sk]):
            r = readings[sk]
            out[sk] = {k: r.get(k, []) for k in ("setpoints", "equipment_data", "notes", "streams")} | {
                "date": (r.get("title_block") or {}).get("date") or None}
    return out


def _date(text: str | None) -> str | None:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text or "")
    return m[0] if m else None


def design_limits(conn, sheets: dict[str, dict], pack: Pack) -> list[tuple[str, Limit]]:
    """The alarm and trip settings the drawings show, as `design` limits on each instrument's point (#475): never
    the limits that count (those come from the DCS and AF), but checked against them, so an alarm moved without
    the drawing following, or never configured, is found."""
    out = []
    for sk, f in feet(conn, sheets, pack).items():
        for sp in f["setpoints"]:
            for t, v in sp["limits"].items():
                out.append(("pid", Limit(f"pid:sheet:{sk}#{sp['tag']}", [("tag", sp["tag"])], t, "design", v,
                                         sp.get("unit"), valid_from=_date(f.get("date")),
                                         note=f"alarm and trip table on {sk}")))
    return out


def design_measurements(conn, sheets: dict[str, dict], pack: Pack) -> list[Measurement]:
    """Design pressure and temperature from the drawings' equipment data tables, and each PFD stream's design case
    pressure, temperature and flow on the item it leaves (location `stream N`), dated by the sheet (#475)."""
    out = []
    for sk, f in feet(conn, sheets, pack).items():
        at = _date(f.get("date"))
        if not at:
            continue
        for e in f["equipment_data"]:
            for q, unit in (("design_pressure", "pressure_unit"), ("design_temperature", "temperature_unit")):
                if e.get(q) is not None:
                    out.append(Measurement(f"pid:sheet:{sk}#{e['tag']}", [("tag", e["tag"])], q, at, e[q],
                                           e.get(unit), method="pid_table"))
        for st in f.get("streams", []):  # a PFD's design case, on the item each stream leaves
            for tag in st.get("from", []):
                for q in ("pressure", "temperature", "flow"):
                    if st.get(q) is not None:
                        out.append(Measurement(f"pid:sheet:{sk}#stream{st['n']}#{tag}", [("tag", tag)],
                                               f"design_case_{q}", at, st[q], st.get(f"{q}_unit"),
                                               method="pfd_design", location=f"stream {st['n']}"))
    return out


def reviews(conn, sheets: dict[str, dict]) -> list[tuple[str, dict]]:
    """Review questions (kind pid_connection) for off-page lines only one side shows."""
    return [(f"{d['sheet']}:{_norm_line(d['line'])}:{d['direction']}",
             {**d, "question": f"{d['sheet']} shows line {d['line']} going {d['direction']} {d['other_sheet']} "
                               f"({d['other'] or 'no tag'}), but {d['other_sheet']} doesn't show it. Is "
                               f"{d['from']} piped to {d['to']}?"})
            for d in join(conn, sheets)["disputed"]]


def cached_edges(conn, sheets: dict[str, dict]) -> list[Edge]:
    """Edges from cached readings whose image hasn't changed: mentions, piping on each sheet, piping across
    sheets both ends agree on (and disputed ones a reviewer accepted), instrument loops, and PFD streams (piping
    marked with the stream number and the PFD)."""
    readings = _readings(conn, sheets)
    edges, streams = [], []  # a PFD's streams go last, so a P&ID's line and off-page details win on the same pair
    for sk, r in readings.items():
        for m in r["mentions"]:
            edges.append(Edge(("ref", f"pid:sheet:{sk}"), "mentions", ("tag", m["key"]), "claude",
                              confidence=m["confidence"], props={"text": m["text"], "method": r["model"]}))
        for c in r.get("connections", []):
            (streams if c.get("stream") else edges).append(Edge(
                ("tag", c["from"]), "pipes_to", ("tag", c["to"]), "claude",
                props={"line": c["line"], "sheet": sk, "method": r["model"], **({"via": c["via"]} if c.get("via") else {}),
                       **({"stream": c["stream"], "pfd": sk} if c.get("stream") else {})}))
        for lp in r.get("loops", []):
            m, reg = lp["members"], lp.get("registered") or []
            if len(m) >= 2 and reg and reg[0] and reg[-1] and m[0] != m[-1]:
                edges.append(Edge(("tag", m[0]), "controls", ("tag", m[-1]), "claude",
                                  props={"loop": m, "sheet": sk, "method": r["model"]}))
    for c in join(conn, sheets, readings)["connections"]:
        if c["registered"] and c["from"] != c["to"]:
            edges.append(Edge(("tag", c["from"]), "pipes_to", ("tag", c["to"]), "claude",
                              props={"line": c["line"], "sheets": c["sheets"], "offpage": True}))
    for row in conn.execute("select payload from review_item where kind = 'pid_connection' and status = 'decided' "
                            "and decision->>'decision' = 'accept'"):
        p = row["payload"]
        if p.get("registered") and p.get("from") and p.get("to"):
            edges.append(Edge(("tag", p["from"]), "pipes_to", ("tag", p["to"]), "review",
                              props={"line": p.get("line"), "sheets": [p["sheet"], p["other_sheet"]], "offpage": True}))
    return edges + streams


def score(conn, truth: dict) -> dict:
    """Compare what was read from each drawing with the answer key {sheet: {tags: [{key}], lines:
    [{from, to}]}} (or {sheet: [tag keys]}): tags linked, and piping read. Smart P&IDs aren't read by
    Claude, so they aren't scored."""
    formats = {r["source_key"]: r["f"] for r in conn.execute(
        "select source_key, payload->>'format' as f from source_record where source = 'pid' and record_type = 'sheet'")}
    truth = {s: k for s, k in truth.items() if formats.get(s, "image") != "smart"}
    found: dict[str, set[str]] = {}
    for r in conn.execute(
        """
        select c.source_key as sheet, l.label as tag
        from edge e
        join crosswalk c on c.fuzer_id = e.src and c.source = 'pid' and c.record_type = 'sheet'
        join entity_label l on l.fuzer_id = e.dst
        where e.rel = 'mentions' and 'claude' = any(e.sources)
        """
    ):
        found.setdefault(r["sheet"], set()).add(r["tag"])
    pipes = {r["source_key"]: {(c["from"], c["to"]) for c in r["result"].get("connections", [])}
             for r in conn.execute("select source_key, result from llm_extraction where source = 'pid'")}
    per_sheet, tp, fp, fn, ptp, pfp, pfn = [], 0, 0, 0, 0, 0, 0
    for sheet, key in truth.items():
        expected = [x["key"] for x in key["tags"]] if isinstance(key, dict) else key
        exp, got = set(expected), found.get(sheet, set())
        tp += len(exp & got)
        fp += len(got - exp)
        fn += len(exp - got)
        if exp - got or got - exp:
            per_sheet.append({"sheet": sheet, "missed": sorted(exp - got), "wrong": sorted(got - exp)})
        if isinstance(key, dict) and sheet in pipes:
            want, read = {(x["from"], x["to"]) for x in key.get("lines", [])}, pipes[sheet]
            ptp, pfp, pfn = ptp + len(want & read), pfp + len(read - want), pfn + len(want - read)
    read = sum(1 for s in truth if s in found)
    note = None
    if not read:
        loaded = conn.execute("select count(*) as n from source_record where source = 'pid' and payload ? 'image'").fetchone()["n"]
        note = ("no drawings loaded: generate and ingest data with drawings (uv run fakeplant demo)" if not loaded
                else "nothing read yet: uv run fuzer llm-link --source pids")
    elements, by_level = _elements(conn, truth)
    return {
        **({"note": note} if note else {}),
        "elements": elements, "by_level": by_level,
        "sheets": len(truth), "sheets_read": read,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "piping": {"recall": round(ptp / (ptp + pfn), 3) if ptp + pfn else None,
                   "precision": round(ptp / (ptp + pfp), 3) if ptp + pfp else None},
        "problems": per_sheet[:20],
    }


# The benchmark as an eval (#475): a run fails when an element's recall or precision falls under its floor, or
# more than DROP below the last recorded run of the same reader. Floors hold for any reader worth keeping; the
# drop check is what judges a prompt or pipeline change against the run before it.
FLOORS = {"tags": 0.8, "connections": 0.6, "offpage": 0.5, "loops": 0.5, "line_numbers": 0.6, "setpoints": 0.7,
          "equipment_data": 0.7, "notes": 0.7, "streams": 0.6}
DROP = 0.05


def gate(scores: dict, previous: dict | None = None) -> list[str]:
    """What fails in a pid-score result: elements under their floor, and elements that fell more than DROP since
    `previous` (an earlier result of the same reader)."""
    bad = []
    for e, v in (scores.get("elements") or {}).items():
        for m in ("recall", "precision"):
            if v.get(m) is not None and e in FLOORS and v[m] < FLOORS[e]:
                bad.append(f"{e} {m} {v[m]} under {FLOORS[e]}")
            was = ((previous or {}).get("elements") or {}).get(e, {}).get(m)
            if v.get(m) is not None and was is not None and v[m] < was - DROP:
                bad.append(f"{e} {m} fell from {was} to {v[m]}")
    return bad


def _norm_note(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _pr(tp: int, fp: int, fn: int) -> dict:
    return {"recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None, "expected": tp + fn}


def _elements(conn, truth: dict) -> tuple[dict, dict]:
    """Precision and recall per element type (tags, in-sheet connections, connections across sheets, loops,
    line numbers, alarm and trip settings, design ratings, notes) overall and by degradation level, from the answer keys' new fields. Sheets whose key predates
    them (no `level`) only count for tags."""
    results = {r["source_key"]: r["result"] for r in conn.execute(
        "select source_key, result from llm_extraction where source = 'pid'")}
    joined = {(c["from"], c["to"]) for c in join(conn, {
        r["source_key"]: r["payload"] for r in conn.execute(
            "select source_key, payload from source_record where source = 'pid' and record_type = 'sheet'")},
        )["connections"]}
    counts: dict[tuple[str, str], list[int]] = {}

    def add(level, element, want: set, got: set):
        for k in (("all", element), (level, element)):
            c = counts.setdefault(k, [0, 0, 0])
            c[0] += len(want & got)
            c[1] += len(got - want)
            c[2] += len(want - got)

    for sheet, key in truth.items():
        if not isinstance(key, dict) or "level" not in key or sheet not in results:
            continue
        r, level = results[sheet], key["level"]
        visible = [t for t in key["tags"] if not t.get("hidden")]
        add(level, "tags", {t["key"] for t in visible}, {m["key"] for m in r["mentions"]})
        add(level, "connections", {(c["from"], c["to"]) for c in key["connections"] if c.get("to") and not c.get("offpage")},
            {(c["from"], c["to"]) for c in r.get("connections", [])})
        add(level, "offpage", {(c["from"], c["to"]) for c in key["connections"] if c.get("offpage")},
            {(a, b) for a, b in joined if {a, b} & {t["key"] for t in key["tags"]}})
        add(level, "loops", {tuple(lp["members"]) for lp in key["loops"]},
            {tuple(lp["members"]) for lp in r.get("loops", [])})
        add(level, "line_numbers", {_norm_line(c["line"]) for c in key["connections"] if c.get("line")},
            {_norm_line(ln["text"]) for ln in r.get("lines", [])})
        if "tables" in key:  # data tables and notes (#475): each setting, each design rating, each note
            want_sp, want_eq = set(), set()
            for t in key["tables"]:
                for row in t["rows"]:
                    if row.get("hidden"):
                        continue
                    if t["kind"] == "streams":
                        continue
                    if t["kind"] == "setpoints":
                        want_sp |= {(row["key"], typ, v) for typ, c in zip(("lolo", "lo", "hi", "hihi"), row["cells"][1:5],
                                                                          strict=True) if (v := _number(c)) is not None}
                    else:
                        want_eq.add((row["key"], _number(row["cells"][2]), _number(row["cells"][3])))
            add(level, "setpoints", want_sp, {(sp["tag"], typ, v) for sp in r.get("setpoints", [])
                                              for typ, v in sp["limits"].items()})
            add(level, "equipment_data", want_eq, {(e["tag"], e["design_pressure"], e["design_temperature"])
                                                   for e in r.get("equipment_data", [])})
            if key.get("kind") == "pfd":  # each stream's design pressure and temperature
                hidden = any(r.get("hidden") for t in key["tables"] for r in t["rows"])
                add(level, "streams", set() if hidden else
                    {(str(st["n"]), _number(st["pressure"]), _number(st["temperature"])) for st in key["streams"]},
                    {(st["n"], st.get("pressure"), st.get("temperature")) for st in r.get("streams", [])})
            add(level, "notes", {_norm_note(n["text"]) for n in key["notes"] if not n.get("hidden")},
                {_norm_note(n["text"]) for n in r.get("notes", [])})
    elements = {e: _pr(*c) for (lv, e), c in counts.items() if lv == "all"}
    by_level: dict[str, dict] = {}
    for (lv, e), c in counts.items():
        if lv != "all":
            by_level.setdefault(lv, {})[e] = _pr(*c)
    return elements, by_level
