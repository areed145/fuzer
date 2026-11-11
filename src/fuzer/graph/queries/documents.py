"""Documents and P&ID drawings: revisions, drawings for a unit, one sheet with its marks."""

from pathlib import Path


def documents_with_several_current_revisions(conn, limit: int = 50) -> list[dict]:
    """Document numbers the EDMS lists as current in more than one revision."""
    return conn.execute(
        """
        select n.value #>> '{}' as doc_number, array_agg(l.label order by l.label) as revisions
        from golden n
        join golden st on st.fuzer_id = n.fuzer_id and st.field = 'status' and st.value #>> '{}' = 'current'
        join entity e on e.fuzer_id = n.fuzer_id and e.kind = 'document' and e.retired_at is null
        left join entity_label l on l.fuzer_id = n.fuzer_id
        where n.field = 'doc_number'
        group by 1 having count(*) > 1
        order by 1 limit %s
        """,
        (limit,),
    ).fetchall()


def drawings_for(conn, fuzer_id: str, limit: int = 10) -> dict:
    """The P&ID sheets that show an entity (for equipment, the tag it's installed at now), and which
    entity ids to highlight on them."""
    ids = [fuzer_id] + [r["dst"] for r in conn.execute(
        "select dst from edge where src = %s and rel = 'installed_at' and valid_to is null", (fuzer_id,))]
    rows = conn.execute(
        """
        select d.fuzer_id as id, l.label, gt.value #>> '{}' as title, max(m.confidence) as confidence
        from edge m
        join entity d on d.fuzer_id = m.src and d.kind = 'document' and d.retired_at is null
        join crosswalk c on c.fuzer_id = d.fuzer_id and c.source = 'pid' and c.record_type = 'sheet'
        left join entity_label l on l.fuzer_id = d.fuzer_id
        left join golden gt on gt.fuzer_id = d.fuzer_id and gt.field = 'title'
        where m.rel = 'mentions' and m.dst = any(%s)
        group by d.fuzer_id, l.label, gt.value
        order by l.label limit %s
        """,
        (ids, limit),
    ).fetchall()
    return {"highlight": ids, "drawings": rows}


def drawing(conn, fuzer_id: str) -> dict | None:
    """A P&ID drawing and what was read from it, placed on the sheet: each tag (linked to its entity,
    not in the register, or not a tag) and line number, the piping, and findings. From Claude's cached
    reading for scans and text PDFs, from the file itself for smart P&IDs."""
    from PIL import Image

    rec = conn.execute(
        """
        select r.source_key, r.payload from crosswalk c join source_record r using (source, record_type, source_key)
        where c.fuzer_id = %s and c.source = 'pid' and c.record_type = 'sheet'
        """, (fuzer_id,)).fetchone()
    if not rec or not rec["payload"].get("image_path") or not Path(rec["payload"]["image_path"]).is_file():
        return None
    p = rec["payload"]
    width, height = Image.open(p["image_path"]).size
    reading = conn.execute("select model, text_hash, result from llm_extraction where source = 'pid' and source_key = %s",
                           (rec["source_key"],)).fetchone()
    if reading and reading["text_hash"] != p.get("image_sha256"):
        reading = None  # the drawing changed since it was read
    marks, connections, findings, read_by, offpage, loops = [], [], [], None, [], []
    tables, notes = [], []
    if p.get("format") == "smart":
        tables = [{"title": t.get("title"), "columns": t.get("columns", []),
                   "rows": [r["cells"] if isinstance(r, dict) else r for r in t.get("rows", [])]} for t in p.get("tables", [])]
        notes = [{"n": n.get("n"), "text": n.get("text"), "tags": n.get("tags", [])} for n in p.get("notes", [])]
        offpage = [dict(o) for o in p.get("offpage", [])]
        read_by = "smart file"
        for key, (x, y) in (p.get("positions") or {}).items():
            marks.append({"text": key, "key": key, "status": "linked", "x": x, "y": y})
        connections = [{"from": c["from"], "to": c["to"], "line": c.get("line")} for c in p.get("connections", [])]
    elif reading:
        r = reading["result"]
        read_by = reading["model"]
        marks += [{"text": m["text"], "key": m["key"], "status": "linked", "confidence": m.get("confidence"),
                   **({"x": m["x"], "y": m["y"]} if "x" in m else {}), **({"on": m["on"]} if m.get("on") else {})}
                  for m in r["mentions"]]
        marks += [{"text": f["text"], "key": f.get("key"), "status": f["issue"].replace(" ", "_"),
                   **({"x": f["x"], "y": f["y"]} if "x" in f else {})}
                  for f in r["findings"] if f["kind"] not in ("connection", "title_block")]
        marks += [{"text": ln["text"] if isinstance(ln, dict) else ln, "status": "line",
                   **({"x": ln["x"], "y": ln["y"]} if isinstance(ln, dict) and "x" in ln else {})} for ln in r["lines"]]
        connections = r.get("connections", [])
        findings = [f for f in r["findings"] if f["kind"] in ("connection", "title_block")]
        offpage = [dict(o) for o in r.get("offpage", [])]
        loops = r.get("loops", [])
        tables, notes = r.get("tables", []), r.get("notes", [])
    keys = {m["key"] for m in marks if m.get("key")} | {c[e] for c in connections for e in ("from", "to")}
    ids = {r["key"]: r["fuzer_id"] for r in conn.execute(
        "select key, fuzer_id from entity_key where kind = 'tag' and key = any(%s)", (list(keys),))}
    for m in marks:
        if m.get("key") and m["status"] == "linked":
            m["id"] = ids.get(m["key"])
    for c in connections:
        c["from_id"], c["to_id"] = ids.get(c["from"]), ids.get(c["to"])
    if offpage:  # each off-page end: whether the other sheet shows it too, and that sheet's document to open
        from ...sources import llm_pids

        sheets = {r["source_key"]: r["payload"] for r in conn.execute(
            "select source_key, payload from source_record where source = 'pid' and record_type = 'sheet'")}
        joined = llm_pids.join(conn, sheets)
        confirmed = {(c["from"], c["to"]) for c in joined["connections"]}
        disputed = {(d["sheet"], d["direction"], d.get("line")) for d in joined["disputed"]}
        docs = {r["source_key"]: r["fuzer_id"] for r in conn.execute(
            "select source_key, fuzer_id from crosswalk where source = 'pid' and record_type = 'sheet'")}
        for o in offpage:
            pair = (o.get("tag"), o.get("other")) if o["direction"] == "to" else (o.get("other"), o.get("tag"))
            o["status"] = ("confirmed" if pair in confirmed else
                           "one_sided" if (rec["source_key"], o["direction"], o.get("line")) in disputed else "unread")
            o["sheet_id"] = docs.get((o.get("sheet") or "").replace(" ", "").upper())
    return {"sheet": rec["source_key"], "format": p.get("format"), "title": p.get("title"), "read_by": read_by,
            "image": f"/documents/{fuzer_id}/image", "width": width, "height": height, "marks": marks,
            "connections": connections, "findings": findings, "offpage": offpage, "loops": loops,
            "tables": tables, "notes": notes, "checks": _placements(conn, marks)}


def _placements(conn, marks: list[dict]) -> list[dict]:
    """Instruments the drawing shows on one item while the register has them part of another (#475): a field
    change the maintenance system never caught, or a drawing that's wrong. Either way, work raised on the
    instrument goes to the item the register names."""
    on = {m["key"]: m["on"] for m in marks if m.get("on") and m.get("key")}
    if not on:
        return []
    rows = conn.execute(
        """
        select ls.label as tag, e.src as id, ld.label as parent, e.sources from edge e
        join entity_label ls on ls.fuzer_id = e.src join entity_label ld on ld.fuzer_id = e.dst
        where e.rel = 'part_of' and e.valid_to is null and ls.label = any(%s)
        """, (list(on),)).fetchall()
    parents: dict[str, list] = {}
    for r in rows:
        parents.setdefault(r["tag"], []).append(r)
    out = []
    for tag, where in sorted(on.items()):
        ps = parents.get(tag, [])
        if ps and where not in {p["parent"] for p in ps}:
            out.append({"tag": tag, "point": None, "point_id": ps[0]["id"], "type": "placement",
                        "detail": f"the drawing shows it on {where}; " + ", ".join(
                            f"{'/'.join(p['sources'])} has it under {p['parent']}" for p in ps)})
    return out

