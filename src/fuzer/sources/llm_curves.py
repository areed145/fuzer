"""Claude reads OEM datasheets into curves (#92): a pump's head / efficiency / NPSHr / power against
flow, or a choke's Cv against opening, with the rating figures (BEP, operating regions, max Cv, ...).

Each current datasheet document (doc_type datasheet) is sent once: its page images (scanned sheets
have no text) and its text layer when it has one. Results are cached in llm_extraction (source
'datasheet', keyed by the file's hash) and never requested during rebuilds. Every reading becomes a
`curve` review item; only what an engineer accepts (curve_digitised) reaches equipment_curve and the
charts. `fuzer eval` scores readings against the fake plant's true curves (answer_keys/curves.json).
"""

import base64
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from psycopg.types.json import Jsonb

from ..core.pack import Pack
from ..llm_links import DEFAULT_EFFORT, DEFAULT_MODEL, call, usage
from .extract import model_key

PROMPT_VERSION = "2026-11-01.1"
CACHE = "datasheet"
SERIES = ["head", "efficiency", "npshr", "power", "cv"]
RATINGS = ["bep", "mcsf", "por", "aor", "rated_speed", "impeller", "cv_max", "trim_size", "xt"]

SYSTEM_PROMPT = """\
You read OEM equipment datasheets for an oil and gas asset and return the performance curves on them.

Pump curves: head (m), efficiency (%), NPSHr (m) and power (kW) against flow (m3/h). Choke
characteristics: Cv against opening (%). When the sheet has a data table, copy the table exactly.
When it only has a drawn curve, read points off the curve: about 10 per series, spread over the
whole curve from its first to its last point, using the axis gridlines to read values carefully.
Report values in the units on the sheet and name them.

Ratings: bep (x = flow, y = head), mcsf (x = flow), por and aor (x = low flow, y = high flow),
rated_speed (x = rpm), impeller (x = mm), cv_max (x), trim_size (x = inches), xt (x). Use y = 0 when
a rating has only one value. Leave out anything the sheet doesn't show; never guess a value.

read_from: "table" if you copied a table, "curve" if you read points off a drawing, "both" if both."""


def _schema() -> dict:
    point = {"type": "object", "additionalProperties": False, "required": ["x", "y"],
             "properties": {"x": {"type": "number"}, "y": {"type": "number"}}}
    return {"type": "object", "additionalProperties": False,
            "required": ["kind", "manufacturer", "model", "x_unit", "series", "ratings", "read_from", "notes"],
            "properties": {
                "kind": {"type": "string", "enum": ["pump", "choke"]},
                "manufacturer": {"type": "string"}, "model": {"type": "string"}, "x_unit": {"type": "string"},
                "series": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["name", "unit", "points"],
                    "properties": {"name": {"type": "string", "enum": SERIES}, "unit": {"type": "string"},
                                   "points": {"type": "array", "items": point}}}},
                "ratings": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["name", "x", "y"],
                    "properties": {"name": {"type": "string", "enum": RATINGS}, "x": {"type": "number"},
                                   "y": {"type": "number"}}}},
                "read_from": {"type": "string", "enum": ["table", "curve", "both"]},
                "notes": {"type": "string"}}}


def page_content(path: Path) -> tuple[str, list[tuple[str, bytes]]]:
    """A datasheet's text layer and its page images (media type, bytes)."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    text = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
    images = []
    for p in reader.pages:
        for im in p.images:
            ext = im.name.rsplit(".", 1)[-1].lower()
            media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext)
            if media:
                images.append((media, im.data))
    return text, images


def normalise(result: dict) -> dict:
    """A reading as stored for review and use: {curve, series: {name: [[x, y]]}, ratings, units, ...}."""
    return {"curve": result["kind"], "manufacturer": result["manufacturer"], "model": result["model"],
            "read_from": result["read_from"], "notes": result.get("notes", ""),
            "units": {"x": result["x_unit"], **{s["name"]: s["unit"] for s in result["series"]}},
            "series": {s["name"]: sorted([p["x"], p["y"]] for p in s["points"]) for s in result["series"]},
            "ratings": {r["name"]: [r["x"], r["y"] or None] for r in result["ratings"]}}


def run(conn, pack: Pack, *, client=None, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
        limit: int | None = None, workers: int = 4, force: bool = False, dry_run: bool = False, **_) -> dict:
    """Read datasheets without a current cached reading; queue each reading for review."""
    rows = conn.execute(
        """
        select r.source_key, r.payload, x.text_hash, x.prompt_version
        from source_record r left join llm_extraction x on x.source = %s and x.source_key = r.source_key
        where r.source = 'edms' and r.record_type = 'document' and r.payload->>'doc_type' = 'datasheet'
          and coalesce(r.payload->>'status', 'current') = 'current' and r.payload->>'file_sha256' is not null
        order by r.source_key
        """, (CACHE,)).fetchall()
    todo = [r for r in rows if force or r["prompt_version"] != PROMPT_VERSION
            or r["text_hash"] != r["payload"]["file_sha256"][:16]][:limit]
    stats = {"datasheets": len(todo), "model": model, "effort": effort}
    if dry_run or not todo:
        stats["queued_for_review"] = queue_reviews(conn, pack) if not dry_run else 0
        return stats
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    def request(r):
        text, images = page_content(Path(r["payload"]["file_path"]))
        p = r["payload"]
        content = [{"type": "image", "source": {"type": "base64", "media_type": m, "data": base64.b64encode(b).decode()}}
                   for m, b in images[:4]]
        content.append({"type": "text", "text": f"Datasheet {p.get('doc_number')}: {p.get('title')} "
                        f"({p.get('manufacturer')} {p.get('model')}).\n\nText layer:\n{text or '(none: a scan)'}"})
        return {"model": model, "max_tokens": 8000,
                "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": content}],
                "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": _schema()}}}

    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "failed_requests": 0, "stored": 0}
    errors: list[str] = []

    def handle(r, message):
        if message.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"stopped: {message.stop_reason}")
        result = normalise(json.loads(next(b.text for b in message.content if b.type == "text")))
        conn.execute(
            """
            insert into llm_extraction (source, source_key, text_hash, model, prompt_version, result)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (source, source_key) do update set text_hash = excluded.text_hash, model = excluded.model,
                prompt_version = excluded.prompt_version, result = excluded.result, created_at = now()
            """, (CACHE, r["source_key"], r["payload"]["file_sha256"][:16], model, PROMPT_VERSION, Jsonb(result)))
        conn.commit()
        for k, v in usage(message).items():
            totals[k] += v
        totals["stored"] += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(call, client, request(r)): r for r in todo}
        for fut in as_completed(futures):
            try:
                handle(futures[fut], fut.result())
            except Exception as e:  # one unreadable sheet shouldn't stop the others
                totals["failed_requests"] += 1
                errors.append(f"{futures[fut]['source_key']}: {type(e).__name__}: {e}")
    return {**stats, **totals, "queued_for_review": queue_reviews(conn, pack), "errors": errors[:5]}


def queue_reviews(conn, pack: Pack) -> int:
    """A `curve` review item per cached reading not yet accepted (refreshed while still open)."""
    n = 0
    for r in conn.execute(
        """
        select x.source_key, x.result, x.text_hash, r.payload, c.fuzer_id as document
        from llm_extraction x
        join source_record r on r.source = 'edms' and r.record_type = 'document' and r.source_key = x.source_key
        left join crosswalk c on c.source = 'edms' and c.record_type = 'document' and c.source_key = x.source_key
        where x.source = %s and not exists (select 1 from curve_digitised d where d.document_key = x.source_key
                                            and d.result->>'file_hash' = x.text_hash)
        """, (CACHE,)).fetchall():
        p = r["payload"]
        payload = {"document": r["document"], "doc_number": p.get("doc_number"), "title": p.get("title"),
                   "file_hash": r["text_hash"], "model_key": model_key(f"{p.get('manufacturer')}|{p.get('model')}", pack),
                   "manufacturer": p.get("manufacturer"), "model": p.get("model"), "reading": r["result"]}
        n += conn.execute(
            """
            insert into review_item (kind, subject, payload) values ('curve', %s, %s)
            on conflict (kind, subject) do update set payload = excluded.payload, status = 'open'
            where review_item.status = 'open' or review_item.payload->>'file_hash' <> excluded.payload->>'file_hash'
            """, (r["source_key"], Jsonb(payload))).rowcount
    conn.commit()
    return n


def accept(conn, item: dict, by: str, edited: dict | None = None) -> None:
    """Keep an accepted reading (as read, or as the engineer corrected it); the next rebuild lands it."""
    p = item["payload"]
    result = {**(edited or p["reading"]), "file_hash": p["file_hash"]}
    conn.execute(
        """
        insert into curve_digitised (document_key, doc_number, model_key, result, accepted_by) values (%s, %s, %s, %s, %s)
        on conflict (document_key) do update set result = excluded.result, model_key = excluded.model_key,
            accepted_by = excluded.accepted_by, accepted_at = now()
        """, (item["subject"], p["doc_number"], p["model_key"], Jsonb(result), by))


def score(conn, truth: list[dict]) -> dict:
    """Readings against the true curves: per datasheet and series, the share of points within 3% of the
    curve (of its range, for values near zero) and the mean error, grouped by how the sheet was made."""
    readings = {r["result"]["model"]: r["result"] for r in conn.execute(
        "select result from llm_extraction where source = %s", (CACHE,))}
    sheets, by_form = [], {}
    for t in truth:
        r = readings.get(t["model"])
        form = ("pdf with table" if "pdf" in t["delivered_as"] else
                "scan with table" if t["kind"] == "choke" else "scan, curve only")
        if not any(f in t["delivered_as"] for f in ("pdf", "scan")):
            continue
        if r is None:
            sheets.append({"datasheet": t["datasheet"], "form": form, "read": False})
            continue
        from ..curves import at

        per_series = {}
        for name, true_pts in t["series"].items():
            if name not in t.get("on_sheet", t["series"]):  # not drawn or tabled on this sheet
                continue
            got = r["series"].get(name, [])
            span = max(y for _, y in true_pts) - min(y for _, y in true_pts) or 1
            errs = []
            for x, y in got:
                ty = at(true_pts, x)
                if ty is not None:
                    errs.append(abs(y - ty) / max(abs(ty), 0.05 * span))
            per_series[name] = {"points": len(got), "within_3pct": round(sum(e <= 0.03 for e in errs) / len(errs), 3)
                                if errs else 0.0, "mean_error_pct": round(100 * sum(errs) / len(errs), 2) if errs else None}
        ok = [s["within_3pct"] for s in per_series.values()]
        sheets.append({"datasheet": t["datasheet"], "form": form, "read": True, "read_from": r.get("read_from"),
                       "within_3pct": round(sum(ok) / len(ok), 3) if ok else 0.0, "series": per_series})
        by_form.setdefault(form, []).append(sheets[-1]["within_3pct"])
    return {"datasheets": len(sheets), "read": sum(s["read"] for s in sheets),
            "within_3pct_by_form": {f: round(sum(v) / len(v), 3) for f, v in by_form.items()}, "sheets": sheets}
