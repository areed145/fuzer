"""Claude reads P&ID drawings (scans and clean exports): tags, piping and where each tag sits.

Each drawing is cut into overlapping tiles (small text stays readable after the model's image
resizing) and sent in one request. Claude transcribes every tag as drawn; Fuzer then turns each
reading into a canonical key with the asset pack's tag grammar (area from the sheet) and checks it
against the register. Readings that are not tags, or tags missing from the register, are kept as
findings instead of becoming links. Claude is deliberately not shown the expected tags, so it
can't "find" ones that aren't on the drawing.

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

from .llm_links import DEFAULT_EFFORT, DEFAULT_MODEL, call, run_batch, usage
from .model import Edge
from .pack import Pack

PROMPT_VERSION = "2026-10-30.2"
TILE_TARGET = 1400  # source pixels per tile side before overlap
MAX_EDGE = 1568  # long edge sent per tile
OVERLAP = 0.10

SYSTEM_PROMPT = """\
You transcribe tag numbers from scanned piping and instrumentation diagrams (P&IDs) of an oil and gas
facility. The drawing comes as overlapping tiles in reading order.

Report every tag you can read:
- instrument bubbles: function letters on the top line, loop number with any suffix letter on the
  bottom line. Write them FUNCTION-LOOP, e.g. PT-1001A. kind "instrument".
- equipment tags written by vessels, pumps, compressors and exchangers, e.g. 44-P-1001A. kind "equipment".
- line numbers on process lines, e.g. 6"-P-44123-A1. kind "line".
- anything else that looks like an identifier: kind "other".

Also report connections: each process line between two pieces of equipment, as from / to (the tags
at each end, following the flow arrow) and the line number written on it.

Write each tag exactly as drawn and don't add an area or prefix that isn't written. Report a tag once
even if it appears on several tiles, giving the tile number where it is clearest and the centre of
the label within that tile as x and y in that tile's pixels (each tile's size is given; 0, 0 is its
top-left corner). If a character is
unclear, give your best reading and a lower confidence (0 to 1). Also read the title block."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title_block", "tags", "connections"],
    "properties": {
        "title_block": {
            "type": "object",
            "additionalProperties": False,
            "required": ["drawing_number", "title", "revision"],
            "properties": {"drawing_number": {"type": "string"}, "title": {"type": "string"},
                           "revision": {"type": "string"}},
        },
        "connections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["from", "to", "line"],
            "properties": {"from": {"type": "string"}, "to": {"type": "string"}, "line": {"type": "string"}},
        }},
        "tags": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "kind", "confidence", "tile", "x", "y"],
            "properties": {
                "text": {"type": "string"},
                "kind": {"type": "string", "enum": ["instrument", "equipment", "line", "other"]},
                "confidence": {"type": "number"},
                "tile": {"type": "integer"},
                "x": {"type": "number"},
                "y": {"type": "number"},
            },
        }},
    },
}


def tile_boxes(w: int, h: int) -> list[tuple[int, int, int, int]]:
    """Overlapping tile boxes (left, top, right, bottom) covering a w x h drawing, in reading order."""
    cols, rows = max(1, math.ceil(w / TILE_TARGET)), max(1, math.ceil(h / TILE_TARGET))
    tw, th = w / cols, h / rows
    return [(max(0, int(c * tw - tw * OVERLAP)), max(0, int(r * th - th * OVERLAP)),
             min(w, int((c + 1) * tw + tw * OVERLAP)), min(h, int((r + 1) * th + th * OVERLAP)))
            for r in range(rows) for c in range(cols)]


def tiles(path: str) -> list[tuple[str, bytes, tuple[int, int, int, int]]]:
    """Overlapping PNG tiles of a drawing, each no larger than MAX_EDGE on its long side, with the
    box (in sheet pixels) each was cut from."""
    img = Image.open(path).convert("L")
    w, h = img.size
    boxes = tile_boxes(w, h)
    cols = max(1, math.ceil(w / TILE_TARGET))
    rows = len(boxes) // cols
    out = []
    for i, box in enumerate(boxes):
        tile = img.crop(box)
        if max(tile.size) > MAX_EDGE:
            tile.thumbnail((MAX_EDGE, MAX_EDGE))
        buf = io.BytesIO()
        tile.save(buf, format="PNG", optimize=True)
        label = f"{i + 1} (row {i // cols + 1} of {rows}, column {i % cols + 1} of {cols}, {tile.size[0]} x {tile.size[1]} px)"
        out.append((label, buf.getvalue(), box))
    return out


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


def _area(sheet: dict) -> str | None:
    m = re.search(r"\b(\d{2})\b", sheet.get("title", ""))
    return m.group(1) if m else None


def interpret(data: dict, sheet: dict, pack: Pack, known_tags: set[str],
              boxes: list[tuple[int, int, int, int]] | None = None) -> dict:
    """Claude's readings -> links to known tags, plus findings for everything else. With the tile
    boxes, each reading's position in its tile (pixels as sent) becomes a position on the sheet."""
    area = _area(sheet)
    mentions, findings, lines, seen = [], [], [], set()

    def at(t):
        if not boxes or not isinstance(t.get("tile"), int) or not 1 <= t["tile"] <= len(boxes):
            return {}
        x0, y0, x1, y1 = boxes[t["tile"] - 1]
        scale = min(1.0, MAX_EDGE / max(x1 - x0, y1 - y0))  # tiles larger than MAX_EDGE were shrunk before sending
        x = min(max(t.get("x", 0) / scale, 0), x1 - x0)
        y = min(max(t.get("y", 0) / scale, 0), y1 - y0)
        return {"x": round(x0 + x), "y": round(y0 + y)}

    for t in data["tags"]:
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
            mentions.append({"text": t["text"], "kind": "tag", "key": key, "confidence": t["confidence"], **at(t)})
    connections = []
    for c in data.get("connections", []):
        a, b = pack.tags.parse(c["from"], default_area=area), pack.tags.parse(c["to"], default_area=area)
        if a in known_tags and b in known_tags and a != b:
            connections.append({"from": a, "to": b, "line": c["line"]})
        else:
            findings.append({"text": f"{c['from']} -> {c['to']}", "kind": "connection", "issue": "end not in register"})
    tb = data["title_block"]
    if tb["drawing_number"] and tb["drawing_number"].replace(" ", "") != sheet["sheet"]:
        findings.append({"text": tb["drawing_number"], "kind": "title_block", "issue": "drawing number differs from register"})
    if tb["revision"] and sheet.get("revision") and tb["revision"].strip().upper() != str(sheet["revision"]).upper():
        findings.append({"text": tb["revision"], "kind": "title_block", "issue": "revision differs from register"})
    out = {"title_block": tb, "mentions": mentions, "findings": findings, "lines": lines, "connections": connections}
    if boxes:
        out["size"] = [max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return out


def run(conn, pack: Pack, *, client=None, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
        limit: int | None = None, workers: int = 4, batch: bool = False, force: bool = False,
        dry_run: bool = False, poll_seconds: int = 30) -> dict:
    """Read drawings (all but smart P&IDs) that have no current cached result. Needs a rebuilt register first."""
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
    stats = {"sheets": len(todo), "model": model, "effort": effort, "image_missing": len(missing),
             "already_cached": len(rows) - len(todo) - len(missing) if not force else 0}
    if dry_run or not todo:
        # Images cost about width x height / 750 tokens each.
        n_tiles = sum(len(tiles(r["payload"]["image_path"])) for r in todo[:5]) / max(1, min(len(todo), 5))
        stats["estimated_input_tokens"] = int(len(todo) * n_tiles * MAX_EDGE * MAX_EDGE * 0.75 / 750)
        return stats

    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    known_tags = {r["key"] for r in conn.execute("select key from entity_key where kind = 'tag'")}
    sheets = {r["source_key"]: r["payload"] for r in todo}
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "failed_requests": 0,
              "stored": 0, "tags_linked": 0, "findings": 0}
    errors: list[str] = []

    def handle(sk, message):
        if message.stop_reason == "refusal":
            raise RuntimeError("model declined the request")
        if message.stop_reason == "max_tokens":
            raise RuntimeError("answer cut off at max_tokens")
        data = json.loads(next(b.text for b in message.content if b.type == "text"))
        result = interpret(data, sheets[sk], pack, known_tags, tile_boxes(*Image.open(sheets[sk]["image_path"]).size))
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

    if batch:
        run_batch(client, {sk: _request(model, effort, p) for sk, p in sheets.items()}, handle, totals, errors,
                  poll_seconds)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(call, client, _request(model, effort, p)): sk for sk, p in sheets.items()}
            for fut in as_completed(futures):
                try:
                    handle(futures[fut], fut.result())
                except Exception as e:
                    totals["failed_requests"] += 1
                    errors.append(f"{futures[fut]}: {type(e).__name__}: {e}")
    return {**stats, **totals, "errors": errors[:5]}


def cached_edges(conn, sheets: dict[str, dict]) -> list[Edge]:
    """Mention edges from cached readings whose image hasn't changed."""
    edges = []
    for row in conn.execute("select source_key, text_hash, model, result from llm_extraction where source = 'pid'"):
        payload = sheets.get(row["source_key"])
        if payload is None or row["text_hash"] != payload.get("image_sha256"):
            continue
        for m in row["result"]["mentions"]:
            edges.append(Edge(("ref", f"pid:sheet:{row['source_key']}"), "mentions", ("tag", m["key"]), "claude",
                              confidence=m["confidence"], props={"text": m["text"], "method": row["model"]}))
        for c in row["result"].get("connections", []):
            edges.append(Edge(("tag", c["from"]), "pipes_to", ("tag", c["to"]), "claude",
                              props={"line": c["line"], "sheet": row["source_key"], "method": row["model"]}))
    return edges


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
    return {
        **({"note": note} if note else {}),
        "sheets": len(truth), "sheets_read": read,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "piping": {"recall": round(ptp / (ptp + pfn), 3) if ptp + pfn else None,
                   "precision": round(ptp / (ptp + pfp), 3) if ptp + pfp else None},
        "problems": per_sheet[:20],
    }
