"""Claude reads operator log entries: links the plant they mention and pulls out events.

`run()` calls Claude for entries without a current cached result and stores what it returns in
llm_extraction (as canonical kind + key, so results survive ID merges). `cached_items()` turns
those results into candidates and edges during every rebuild; rebuilds never call the model.

Claude only ever picks from a numbered list of candidates built here from the asset register,
so it can say "not sure" but cannot invent an entity.
"""

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from . import nel
from .model import Candidate, Edge
from .pack import Pack

PROMPT_VERSION = "2026-10-28.1"
DEFAULT_MODEL = os.environ.get("FUZER_LLM_MODEL", "claude-opus-5-5")
DEFAULT_EFFORT = os.environ.get("FUZER_LLM_EFFORT", "medium")
MAX_CANDIDATES = 60
EQUIPMENT_FUNCS = {"P", "K", "V", "E"}

EVENT_TYPES = [
    "trip", "start", "stop", "changeover", "shut_in", "bring_online", "choke_change", "alarm", "leak",
    "maintenance", "work_order", "well_test", "instrument_fault", "other",
]
ROLES = ["subject", "from", "to", "location"]

SYSTEM_PROMPT = """\
You read shift log entries from an offshore oil and gas platform and connect them to the asset register.

Each entry comes with a list of candidates: tags, wells and equipment positions it might refer to, each
with an id (c1, c2, ...), its key and a description. Tags are written area-function-loop-suffix, so
20-P-1001B is pump B, loop 1001, in area 20. Suffix letters A/B/C are parallel units, which is what
operators mean by "the B pump". Wells look like A-07; a sidetrack is "A-07 ST1".

For each entry return:

mentions: every phrase that refers to a specific piece of plant, instrument or well. Set candidate_id to
the candidate it means only when the text and context make that clear; otherwise null. A short tag
such as "PT-2001" that exists in several areas, with nothing in the entry to choose between them, is
null. Never use an id that is not in that entry's list. confidence is 0 to 1. note says briefly why.

events: what happened, one item per distinct occurrence, typed with the closest type. involves lists
the candidates taking part with their role: subject (the thing the event is about), from / to (for
changeovers and swaps), location (where it happened). details holds useful values as name/value
pairs, e.g. choke_from_pct, choke_to_pct, vibration_mm_s, reason, duration. Return no events for an
entry that only records a reading or a routine status.

Return every entry you were given, in the same order, with its entry_id."""

_id = {"type": "string"}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entries"],
    "properties": {"entries": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["entry_id", "mentions", "events"],
        "properties": {
            "entry_id": _id,
            "mentions": {"type": "array", "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "candidate_id", "confidence", "note"],
                "properties": {
                    "text": {"type": "string"},
                    "candidate_id": {"anyOf": [_id, {"type": "null"}]},
                    "confidence": {"type": "number"},
                    "note": {"type": "string"},
                },
            }},
            "events": {"type": "array", "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "summary", "involves", "details"],
                "properties": {
                    "type": {"type": "string", "enum": EVENT_TYPES},
                    "summary": {"type": "string"},
                    "involves": {"type": "array", "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["candidate_id", "role"],
                        "properties": {"candidate_id": _id, "role": {"type": "string", "enum": ROLES}},
                    }},
                    "details": {"type": "array", "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "value"],
                        "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                    }},
                },
            }},
        },
    }}},
}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# --- candidates ---------------------------------------------------------------------------------

@dataclass
class Register:
    """What the asset register knows, loaded once per run, for building candidate lists."""
    known: dict[tuple[str, str], str] = field(default_factory=dict)  # (kind, key) -> description
    by_short: dict[str, list[str]] = field(default_factory=dict)  # "PT-2001" -> ["20-PT-2001", ...]
    areas: list[tuple[str, str]] = field(default_factory=list)  # (area code, name in lower case)
    area_equipment: dict[str, list[str]] = field(default_factory=dict)  # area code -> equipment tag keys


def load_register(conn) -> Register:
    reg = Register()
    rows = conn.execute(
        """
        select ek.kind, ek.key, g.value #>> '{}' as description
        from entity_key ek
        left join golden g on g.fuzer_id = ek.fuzer_id and g.field = 'description'
        where ek.kind in ('tag', 'well', 'area')
        """
    ).fetchall()
    for r in rows:
        reg.known[(r["kind"], r["key"])] = r["description"] or ""
        if r["kind"] == "tag":
            area, short = r["key"].split("-", 1)
            reg.by_short.setdefault(short, []).append(r["key"])
            if short.split("-")[0] in EQUIPMENT_FUNCS:
                reg.area_equipment.setdefault(area, []).append(r["key"])
        elif r["kind"] == "area" and r["description"]:
            # "Area 47 Water Injection 3" -> "water injection 3"
            name = re.sub(r"^area \d+\s*", "", r["description"].lower()).strip()
            reg.areas.append((r["key"].rsplit("-", 1)[-1], name))
    reg.areas.sort(key=lambda a: -len(a[1]))  # longest names first: "water injection 3" before "water injection"
    return reg


def candidates_for(text: str, pack: Pack, reg: Register) -> list[tuple[str, str]]:
    """(kind, key) pairs the entry might refer to, most specific first."""
    out: list[tuple[str, str]] = []

    def add(kind, key):
        if (kind, key) in reg.known and (kind, key) not in out:
            out.append((kind, key))

    for m in nel.find_mentions(text, pack):
        if m.kind == "tag_noarea":
            for key in reg.by_short.get(m.key, []):
                add("tag", key)
        else:
            add(m.kind, m.key)

    lowered, taken = text.lower(), []
    for code, name in reg.areas:
        for hit in re.finditer(rf"\b{re.escape(name)}\b", lowered):
            if any(s <= hit.start() < e for s, e in taken):
                continue  # inside a longer area name already matched
            taken.append((hit.start(), hit.end()))
            add("area", f"{pack.facility}-{code}")
            for key in reg.area_equipment.get(code, []):
                add("tag", key)
    return out[:MAX_CANDIDATES]


def _entry_payload(sk: str, payload: dict, cands: list[tuple[str, str]], reg: Register) -> dict:
    return {
        "entry_id": sk,
        "timestamp": payload.get("timestamp"),
        "author": payload.get("author"),
        "text": payload.get("text", ""),
        "candidates": [{"id": f"c{i + 1}", "kind": k, "key": key, "description": reg.known[(k, key)]}
                       for i, (k, key) in enumerate(cands)],
    }


# --- calling Claude -----------------------------------------------------------------------------

def _request(model: str, effort: str, entries: list[dict]) -> dict:
    return {
        "model": model,
        "max_tokens": 16000,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": json.dumps({"entries": entries}, ensure_ascii=False)}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
    }


def _parse(message, entries: list[dict]) -> tuple[dict[str, dict], dict]:
    """Validate Claude's answer against each entry's candidates. Returns per-entry results in
    canonical (kind, key) form, plus counts of anything dropped."""
    if message.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("answer cut off at max_tokens; use fewer entries per request")
    data = json.loads(next(b.text for b in message.content if b.type == "text"))
    by_id = {e["entry_id"]: {c["id"]: (c["kind"], c["key"]) for c in e["candidates"]} for e in entries}
    results, dropped = {}, {"unknown_entry": 0, "unknown_candidate": 0}
    for item in data["entries"]:
        cands = by_id.get(item["entry_id"])
        if cands is None:
            dropped["unknown_entry"] += 1
            continue

        def resolve(cid):
            if cid is None:
                return None
            if cid not in cands:
                dropped["unknown_candidate"] += 1
                return None
            return cands[cid]

        mentions = []
        for m in item["mentions"]:
            target = resolve(m["candidate_id"])
            mentions.append({"text": m["text"], "kind": target[0] if target else None,
                             "key": target[1] if target else None,
                             "confidence": m["confidence"], "note": m["note"]})
        events = []
        for ev in item["events"]:
            involves = [{"kind": t[0], "key": t[1], "role": inv["role"]}
                        for inv in ev["involves"] if (t := resolve(inv["candidate_id"]))]
            events.append({"type": ev["type"], "summary": ev["summary"], "involves": involves,
                           "details": {d["name"]: d["value"] for d in ev["details"]}})
        results[item["entry_id"]] = {"mentions": mentions, "events": events}
    return results, dropped


def _call(client, model: str, effort: str, entries: list[dict]):
    return client.beta.messages.create(
        **_request(model, effort, entries),
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if the model declines, the API retries on a fallback model
    )


def _usage(message) -> dict:
    u = message.usage
    return {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}


def _store(conn, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into llm_extraction (source, source_key, text_hash, model, prompt_version, result)
            values ('oplog', %s, %s, %s, %s, %s)
            on conflict (source, source_key) do update
            set text_hash = excluded.text_hash, model = excluded.model, prompt_version = excluded.prompt_version,
                result = excluded.result, created_at = now()
            """,
            rows,
        )
    conn.commit()


def run(conn, pack: Pack, *, client=None, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
        limit: int | None = None, per_request: int = 20, workers: int = 4, batch: bool = False,
        force: bool = False, dry_run: bool = False, poll_seconds: int = 30) -> dict:
    """Read log entries that have no current cached result. Needs a rebuilt register first."""
    rows = conn.execute(
        """
        select r.source_key, r.payload, x.text_hash, x.prompt_version
        from source_record r
        left join llm_extraction x on x.source = r.source and x.source_key = r.source_key
        where r.source = 'oplog' and r.record_type = 'entry'
        order by r.source_key
        """
    ).fetchall()
    todo = [r for r in rows if force or r["text_hash"] != text_hash(r["payload"].get("text", ""))
            or r["prompt_version"] != PROMPT_VERSION][:limit]

    reg = load_register(conn)
    entries = [_entry_payload(r["source_key"], r["payload"], candidates_for(r["payload"].get("text", ""), pack, reg), reg)
               for r in todo]
    chunks = [entries[i:i + per_request] for i in range(0, len(entries), per_request)]
    stats = {"entries": len(entries), "requests": len(chunks), "model": model, "effort": effort,
             "already_cached": len(rows) - len(todo) if not force else 0}
    if dry_run or not chunks:
        # Rough size: ~4 characters per token for the entries, plus the instructions per request.
        chars = sum(len(json.dumps(e)) for e in entries)
        stats["estimated_input_tokens"] = chars // 4 + len(chunks) * (len(SYSTEM_PROMPT) // 4)
        return stats

    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    hashes = {r["source_key"]: text_hash(r["payload"].get("text", "")) for r in todo}
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
              "unknown_entry": 0, "unknown_candidate": 0, "failed_requests": 0, "stored": 0}
    errors: list[str] = []

    def handle(chunk, message):
        results, dropped = _parse(message, chunk)
        for k, v in {**_usage(message), **dropped}.items():
            totals[k] += v
        _store(conn, [(sk, hashes[sk], model, PROMPT_VERSION, Jsonb(res)) for sk, res in results.items()])
        totals["stored"] += len(results)

    if batch:
        _run_batch(client, model, effort, chunks, handle, totals, errors, poll_seconds)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_call, client, model, effort, chunk): chunk for chunk in chunks}
            for fut in as_completed(futures):
                try:
                    handle(futures[fut], fut.result())
                except Exception as e:  # one bad request shouldn't lose the rest
                    totals["failed_requests"] += 1
                    errors.append(f"{type(e).__name__}: {e}")
    return {**stats, **totals, "errors": errors[:5]}


def _run_batch(client, model, effort, chunks, handle, totals, errors, poll_seconds):
    """Half-price asynchronous run through the Message Batches API (usually done within an hour)."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    by_id = {f"chunk-{i}": chunk for i, chunk in enumerate(chunks)}
    created = client.messages.batches.create(requests=[
        Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**_request(model, effort, chunk)))
        for cid, chunk in by_id.items()
    ])
    print(f"batch {created.id}: {len(by_id)} requests submitted", flush=True)
    while (b := client.messages.batches.retrieve(created.id)).processing_status != "ended":
        print(f"batch {created.id}: {b.request_counts.processing} still processing", flush=True)
        time.sleep(poll_seconds)
    for result in client.messages.batches.results(created.id):
        if result.result.type == "succeeded":
            try:
                handle(by_id[result.custom_id], result.result.message)
            except Exception as e:
                totals["failed_requests"] += 1
                errors.append(f"{result.custom_id}: {type(e).__name__}: {e}")
        else:
            totals["failed_requests"] += 1
            errors.append(f"{result.custom_id}: {result.result.type}")


# --- rebuild side -------------------------------------------------------------------------------

def cached_items(conn, entries: dict[str, dict]) -> tuple[list[Candidate], list[Edge]]:
    """Candidates (events) and edges from cached results whose entry text hasn't changed."""
    cands, edges = [], []
    for row in conn.execute("select source_key, text_hash, model, result from llm_extraction where source = 'oplog'"):
        payload = entries.get(row["source_key"])
        if payload is None or row["text_hash"] != text_hash(payload.get("text", "")):
            continue
        log = ("ref", f"oplog:entry:{row['source_key']}")
        res = row["result"]
        for m in res["mentions"]:
            if m["key"]:
                edges.append(Edge(log, "mentions", (m["kind"], m["key"]), "claude", confidence=m["confidence"],
                                  props={"text": m["text"], "note": m["note"], "method": row["model"]}))
        for i, ev in enumerate(res["events"]):
            key = f"{row['source_key']}#{i + 1}"
            fields = {"event_type": ev["type"], "summary": ev["summary"], "description": ev["summary"],
                      **({"timestamp": payload["timestamp"]} if payload.get("timestamp") else {}),
                      **{re.sub(r"\W+", "_", k.lower()).strip("_"): v for k, v in ev["details"].items()}}
            event = Candidate("oplog", "event", key, "event", key, fields)
            event.edges.append(Edge(log, "reports", ("ref", event.ref), "claude"))
            for inv in ev["involves"]:
                event.edges.append(Edge(("ref", event.ref), "involves", (inv["kind"], inv["key"]), "claude",
                                        props={"role": inv["role"]}))
            cands.append(event)
    return cands, edges
