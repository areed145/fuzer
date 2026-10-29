"""Golden records: pick one value per entity and field using pack rules.

Source values are rewritten on every run; reviewer choices (source 'review')
are kept and always win.
"""

from collections import defaultdict

from psycopg.types.json import Jsonb
from rapidfuzz import fuzz, utils

from . import db
from .model import Candidate
from .pack import Pack
from .resolve import replace_open_reviews


def _same(a: str, b: str) -> bool:
    # "K.S.B." vs "KSB": equal once spaces are gone; "Siemens" vs "Siemens Energy": token subset.
    return a.replace(" ", "") == b.replace(" ", "") or fuzz.token_set_ratio(a, b) >= 90


def _distinct(values) -> list[str]:
    reps = []
    for v in values:
        s = utils.default_process(str(v))
        if s and not any(_same(s, r) for r in reps):
            reps.append(s)
    return reps


def build(conn, cands: list[Candidate], assigned: dict[str, str], pack: Pack) -> dict:
    conn.execute("delete from assertion where source <> 'review'")
    rows = [(assigned[c.ref], f, Jsonb(v), c.source, c.source_key)
            for c in cands for f, v in c.fields.items()]
    db.copy_rows(conn, "assertion", ["fuzer_id", "field", "value", "source", "source_key"], rows)

    # Group in memory: the source values we just wrote plus reviewer choices already stored.
    grouped = defaultdict(list)
    for fid, field, value, source, source_key in rows:
        grouped[(fid, field)].append({"value": value.obj, "source": source, "source_key": source_key})
    for a in conn.execute("select fuzer_id, field, value, source, source_key from assertion where source = 'review'"):
        grouped[(a["fuzer_id"], a["field"])].append(a)

    golden, conflicts = [], []
    for (fid, field), items in grouped.items():
        rule = pack.rule(field)
        prio = {s: i for i, s in enumerate(rule["priority"])}
        best = min(items, key=lambda a: prio.get(a["source"], len(prio)))
        golden.append((fid, field, Jsonb(best["value"]), best["source"],
                       "review" if best["source"] == "review" else "priority"))
        if rule.get("review_on_conflict") and best["source"] != "review":
            if len(_distinct(a["value"] for a in items)) > 1:
                conflicts.append((f"{fid}:{field}", {
                    "fuzer_id": fid,
                    "field": field,
                    "chosen": best["value"],
                    "values": [{"source": a["source"], "source_key": a["source_key"], "value": a["value"]}
                               for a in items],
                }))

    conn.execute("delete from golden")
    db.copy_rows(conn, "golden", ["fuzer_id", "field", "value", "source", "rule"], golden)
    replace_open_reviews(conn, "conflict", conflicts)
    return {"assertions": len(rows), "conflict_reviews": len(conflicts)}
