"""Claude reads document sections: which ISO 14224 failure modes each helps diagnose or fix, and
whether it is specific to one of the document's models / classes / tags.

Keyword rules already tag sections with failure modes (taxonomy.failure_modes_in); Claude adds what
they miss. Candidates come from what the document applies to (and those models' classes) plus the
tags the section mentions, so Claude can only pick real entities. Results are cached per section in
llm_extraction (source 'edms_section') and merged into the section during every rebuild without
calling the model.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from psycopg.types.json import Jsonb

from .llm_links import DEFAULT_EFFORT, DEFAULT_MODEL, call, run_batch, text_hash, usage
from .model import Candidate, Edge
from .pack import Pack
from .taxonomy import load_standard

PROMPT_VERSION = "2026-10-29.2"
CACHE = "edms_section"

SYSTEM_PROMPT = """\
You read sections of maintenance documents (OEM manuals, troubleshooting guides, procedures) for an
oil and gas asset. For each section return:

failure_modes: the ISO 14224 failure modes (codes from the list given) the section helps diagnose,
prevent or fix. Only modes the section actually addresses; none for sections like Safety or Description.

applies_to: candidate ids the section is specific to, when it is narrower than the whole document (for
example a guide section about one transmitter). Leave it empty when the section applies to everything
the document covers. Only use ids from that section's candidate list.

Return every section given, with its section_id."""


def _schema(codes: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False, "required": ["sections"],
        "properties": {"sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["section_id", "failure_modes", "applies_to"],
            "properties": {
                "section_id": {"type": "string"},
                "failure_modes": {"type": "array", "items": {"type": "string", "enum": codes}},
                "applies_to": {"type": "array", "items": {"type": "string"}},
            },
        }}},
    }


def _candidates(conn, section_keys: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in conn.execute(
        """
        with sec as (
            select c.source_key, c.fuzer_id, p.dst as doc
            from crosswalk c join edge p on p.src = c.fuzer_id and p.rel = 'part_of'
            where c.source = 'edms' and c.record_type = 'section' and c.source_key = any(%s)
        ), targets as (
            select sec.source_key, a.dst as id from sec join edge a on a.src = sec.doc and a.rel = 'applies_to'
            union
            select sec.source_key, m.dst from sec join edge m on m.src = sec.fuzer_id and m.rel = 'mentions'
            union
            select sec.source_key, c.dst from sec
            join edge a on a.src = sec.doc and a.rel = 'applies_to'
            join edge c on c.src = a.dst and c.rel = 'is_a'
        )
        select t.source_key, k.kind, k.key, g.value #>> '{}' as description
        from targets t
        join entity_key k on k.fuzer_id = t.id and k.kind <> 'tag_noarea'
        left join golden g on g.fuzer_id = t.id and g.field = 'description'
        order by t.source_key, k.kind, k.key
        """,
        (section_keys,),
    ):
        lst = out.setdefault(r["source_key"], [])
        lst.append({"id": f"c{len(lst) + 1}", "kind": r["kind"], "key": r["key"], "description": r["description"] or ""})
    return out


def run(conn, pack: Pack, *, client=None, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
        limit: int | None = None, per_request: int = 10, workers: int = 4, batch: bool = False,
        force: bool = False, dry_run: bool = False, poll_seconds: int = 30) -> dict:
    """Read sections that have no current cached result. Needs a rebuilt register first."""
    modes = load_standard(pack.classification.get("taxonomy", "iso14224")).get("failure_modes", {})
    rows = conn.execute(
        """
        select r.source_key, r.payload, x.text_hash, x.prompt_version
        from source_record r
        left join llm_extraction x on x.source = %s and x.source_key = r.source_key
        where r.source = 'edms' and r.record_type = 'section' and coalesce(r.payload->>'status', 'current') = 'current'
        order by r.source_key
        """,
        (CACHE,),
    ).fetchall()
    todo = [r for r in rows if force or r["prompt_version"] != PROMPT_VERSION
            or r["text_hash"] != text_hash(r["payload"].get("text", ""))][:limit]
    cands = _candidates(conn, [r["source_key"] for r in todo])
    entries = [{"section_id": r["source_key"], "heading": r["payload"].get("heading"),
                "text": r["payload"].get("text", ""), "candidates": cands.get(r["source_key"], [])} for r in todo]
    chunks = [entries[i:i + per_request] for i in range(0, len(entries), per_request)]
    stats = {"sections": len(entries), "requests": len(chunks), "model": model, "effort": effort}
    if dry_run or not chunks:
        stats["estimated_input_tokens"] = sum(len(json.dumps(e)) for e in entries) // 4 + len(chunks) * 600
        return stats
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    legend = "Failure mode codes: " + "; ".join(f"{k} = {v}" for k, v in modes.items())

    def request(chunk):
        return {"model": model, "max_tokens": 16000,
                "system": [{"type": "text", "text": f"{SYSTEM_PROMPT}\n\n{legend}", "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": json.dumps({"sections": chunk}, ensure_ascii=False)}],
                "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": _schema(list(modes))}}}

    hashes = {r["source_key"]: text_hash(r["payload"].get("text", "")) for r in todo}
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "failed_requests": 0, "stored": 0,
              "unknown_candidate": 0}
    errors: list[str] = []

    def handle(chunk, message):
        if message.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"stopped: {message.stop_reason}")
        data = json.loads(next(b.text for b in message.content if b.type == "text"))
        by_id = {e["section_id"]: {c["id"]: c for c in e["candidates"]} for e in chunk}
        rows_out = []
        for s in data["sections"]:
            ids = by_id.get(s["section_id"])
            if ids is None:
                continue
            targets = []
            for cid in s["applies_to"]:
                if cid in ids:
                    targets.append({"kind": ids[cid]["kind"], "key": ids[cid]["key"]})
                else:
                    totals["unknown_candidate"] += 1
            result = {"failure_modes": sorted(set(s["failure_modes"]) & set(modes)), "applies_to": targets}
            rows_out.append((CACHE, s["section_id"], hashes[s["section_id"]], model, PROMPT_VERSION, Jsonb(result)))
        for k, v in usage(message).items():
            totals[k] += v
        with conn.cursor() as cur:
            cur.executemany(
                """
                insert into llm_extraction (source, source_key, text_hash, model, prompt_version, result)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (source, source_key) do update
                set text_hash = excluded.text_hash, model = excluded.model, prompt_version = excluded.prompt_version,
                    result = excluded.result, created_at = now()
                """,
                rows_out,
            )
        conn.commit()
        totals["stored"] += len(rows_out)

    if batch:
        by_id = {f"chunk-{i}": c for i, c in enumerate(chunks)}
        run_batch(client, {cid: request(c) for cid, c in by_id.items()}, lambda cid, m: handle(by_id[cid], m),
                  totals, errors, poll_seconds)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(call, client, request(c)): c for c in chunks}
            for fut in as_completed(futures):
                try:
                    handle(futures[fut], fut.result())
                except Exception as e:
                    totals["failed_requests"] += 1
                    errors.append(f"{type(e).__name__}: {e}")
    return {**stats, **totals, "errors": errors[:5]}


def apply_cached(conn, cands: list[Candidate]) -> list[Edge]:
    """Merge cached readings into section candidates (failure modes) and return their extra links.
    Only results whose section text is unchanged are used."""
    sections = {c.source_key: c for c in cands if c.source == "edms" and c.record_type == "section"}
    edges = []
    for row in conn.execute("select source_key, text_hash, model, result from llm_extraction where source = %s",
                            (CACHE,)):
        c = sections.get(row["source_key"])
        if c is None or row["text_hash"] != text_hash(c.fields.get("text", "")):
            continue
        res = row["result"]
        if res["failure_modes"]:
            c.fields["failure_modes"] = sorted(set(c.fields.get("failure_modes", [])) | set(res["failure_modes"]))
        for t in res["applies_to"]:
            edges.append(Edge(("ref", c.ref), "applies_to", (t["kind"], t["key"]), "claude",
                              props={"basis": "section content", "method": row["model"]}))
    return edges
