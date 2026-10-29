"""Claude reads tags off scanned P&ID images.

Each drawing is cut into overlapping tiles (small text stays readable after the model's image
resizing) and sent in one request. Claude transcribes every tag as drawn; Fuzer then turns each
reading into a canonical key with the asset pack's tag grammar (area from the sheet) and checks it
against the register. Readings that are not tags, or tags missing from the register, are kept as
findings instead of becoming links. Claude is deliberately not shown the expected tags, so it
can't "find" ones that aren't on the drawing.

Results are cached in llm_extraction per sheet, keyed by the image's SHA-256, and applied during
rebuilds without calling the model.
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

PROMPT_VERSION = "2026-10-28.1"
TILE_TARGET = 1400  # source pixels per tile side before overlap
MAX_EDGE = 1568  # long edge sent per tile
OVERLAP = 0.10

SYSTEM_PROMPT = """\
You transcribe tag numbers from scanned piping and instrumentation diagrams (P&IDs) of an offshore oil
and gas platform. The drawing comes as overlapping tiles in reading order.

Report every tag you can read:
- instrument bubbles: function letters on the top line, loop number with any suffix letter on the
  bottom line. Write them FUNCTION-LOOP, e.g. PT-1001A. kind "instrument".
- equipment tags written by vessels, pumps, compressors and exchangers, e.g. 44-P-1001A. kind "equipment".
- line numbers on process lines, e.g. 6"-P-44123-A1. kind "line".
- anything else that looks like an identifier: kind "other".

Write each tag exactly as drawn and don't add an area or prefix that isn't written. Report a tag once
even if it appears on several tiles. If a character is unclear, give your best reading and a lower
confidence (0 to 1). Also read the title block."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title_block", "tags"],
    "properties": {
        "title_block": {
            "type": "object",
            "additionalProperties": False,
            "required": ["drawing_number", "title", "revision"],
            "properties": {"drawing_number": {"type": "string"}, "title": {"type": "string"},
                           "revision": {"type": "string"}},
        },
        "tags": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "kind", "confidence"],
            "properties": {
                "text": {"type": "string"},
                "kind": {"type": "string", "enum": ["instrument", "equipment", "line", "other"]},
                "confidence": {"type": "number"},
            },
        }},
    },
}


def tiles(path: str) -> list[tuple[str, bytes]]:
    """Overlapping PNG tiles of a drawing, each no larger than MAX_EDGE on its long side."""
    img = Image.open(path).convert("L")
    w, h = img.size
    cols, rows = max(1, math.ceil(w / TILE_TARGET)), max(1, math.ceil(h / TILE_TARGET))
    tw, th = w / cols, h / rows
    out = []
    for r in range(rows):
        for c in range(cols):
            box = (max(0, int(c * tw - tw * OVERLAP)), max(0, int(r * th - th * OVERLAP)),
                   min(w, int((c + 1) * tw + tw * OVERLAP)), min(h, int((r + 1) * th + th * OVERLAP)))
            tile = img.crop(box)
            if max(tile.size) > MAX_EDGE:
                tile.thumbnail((MAX_EDGE, MAX_EDGE))
            buf = io.BytesIO()
            tile.save(buf, format="PNG", optimize=True)
            label = f"row {r + 1} of {rows}, column {c + 1} of {cols}"
            out.append((label, buf.getvalue()))
    return out


def _request(model: str, effort: str, sheet: dict) -> dict:
    content = [{"type": "text", "text": f"Drawing {sheet['sheet']}. Register title: {sheet.get('title', '')}."}]
    for label, png in tiles(sheet["image_path"]):
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


def interpret(data: dict, sheet: dict, pack: Pack, known_tags: set[str]) -> dict:
    """Claude's readings -> links to known tags, plus findings for everything else."""
    area = _area(sheet)
    mentions, findings, lines, seen = [], [], [], set()
    for t in data["tags"]:
        if t["kind"] == "line":
            lines.append(t["text"])
            continue
        key = pack.tags.parse(t["text"], default_area=area)
        if key is None:
            findings.append({"text": t["text"], "kind": t["kind"], "issue": "not a tag"})
        elif key not in known_tags:
            findings.append({"text": t["text"], "kind": t["kind"], "key": key, "issue": "not in register"})
        elif key not in seen:
            seen.add(key)
            mentions.append({"text": t["text"], "kind": "tag", "key": key, "confidence": t["confidence"]})
    tb = data["title_block"]
    if tb["drawing_number"] and tb["drawing_number"].replace(" ", "") != sheet["sheet"]:
        findings.append({"text": tb["drawing_number"], "kind": "title_block", "issue": "drawing number differs from register"})
    if tb["revision"] and sheet.get("revision") and tb["revision"].strip().upper() != str(sheet["revision"]).upper():
        findings.append({"text": tb["revision"], "kind": "title_block", "issue": "revision differs from register"})
    return {"title_block": tb, "mentions": mentions, "findings": findings, "lines": lines}


def run(conn, pack: Pack, *, client=None, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
        limit: int | None = None, workers: int = 4, batch: bool = False, force: bool = False,
        dry_run: bool = False, poll_seconds: int = 30) -> dict:
    """Read scanned drawings that have no current cached result. Needs a rebuilt register first."""
    rows = conn.execute(
        """
        select r.source_key, r.payload, x.text_hash, x.prompt_version
        from source_record r
        left join llm_extraction x on x.source = r.source and x.source_key = r.source_key
        where r.source = 'pid' and r.record_type = 'sheet' and r.payload->>'format' = 'image'
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
        result = interpret(data, sheets[sk], pack, known_tags)
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
    return edges


def score(conn, truth: dict[str, list[str]]) -> dict:
    """Compare what was linked from each drawing with an answer key {sheet: [tag keys]}."""
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
    per_sheet, tp, fp, fn = [], 0, 0, 0
    for sheet, expected in truth.items():
        exp, got = set(expected), found.get(sheet, set())
        tp += len(exp & got)
        fp += len(got - exp)
        fn += len(exp - got)
        if exp - got or got - exp:
            per_sheet.append({"sheet": sheet, "missed": sorted(exp - got), "wrong": sorted(got - exp)})
    return {
        "sheets": len(truth), "sheets_read": sum(1 for s in truth if s in found),
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "problems": per_sheet[:20],
    }
