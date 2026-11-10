"""Eval for Claude's master data proposals (#476): problems with known answers are planted in a rebuilt project, the
proposals are made, and each planted question is scored.

    plant(conn)                      plant the problems; returns the answer key
    score(conn, key)                 precision and recall of the proposals against it, overall and by confidence
    run(conn, client=None)           plant, propose, score (the fake client `scripted(key)` answers from the key)

Planted:
- refs on open exceptions written the ways other systems write a tag (separators dropped, a hyphen before the
  suffix, a zero-padded loop): each means one register tag;
- a ref that names nothing: the right answer is "none";
- a manufacturer misspelled in the source it won from (a letter dropped): one value, keep the usual spelling.

`score` reports, for each threshold, how many proposals at or above it were right: the number to look at before
setting `stewardship.bulk_accept_at`.

    uv run python -m fuzer.evals.steward_eval            # on the project in FUZER_DATABASE_URL, with the real model
"""

import json
import re
from types import SimpleNamespace

from psycopg.types.json import Jsonb

from ..core import exceptions
from ..graph import proposals

TYPE = "steward_eval"
THRESHOLDS = (0.5, 0.7, 0.8, 0.9, 0.95)


def _variants(key: str) -> list[str]:
    """How other systems write a tag key: no separators, a hyphen before the suffix letter, a zero-padded loop."""
    out = [key.replace("-", "")]
    if m := re.match(r"^(.*-)(\d+)([A-Z])$", key):
        out.append(f"{m[1]}{m[2]}-{m[3]}")
        out.append(f"{m[1]}0{m[2]}{m[3]}")
    elif m := re.match(r"^(.*-)(\d+)$", key):
        out.append(f"{m[1]}0{m[2]}")
    return out


def plant(conn, tags: int = 4) -> dict:
    """Plant the problems; the answer key maps (kind, subject) to the right option's target."""
    key: dict[str, dict] = {}
    exceptions.define_type(conn, TYPE, "eval", "Steward eval")
    rows = conn.execute(
        "select k.key, k.fuzer_id from entity_key k join entity e using (fuzer_id) where k.kind = 'tag' and "
        "e.merged_into is null and e.retired_at is null order by md5(k.key) limit %s", (tags,)).fetchall()
    for i, r in enumerate(rows):
        v = _variants(r["key"])[i % len(_variants(r["key"]))]
        exceptions.raise_exception(conn, TYPE, f"tag-{i}", f"Reading out of range on {v}", "eval", source="eval",
                                   refs=[v])
        key[f"unresolved|{v}"] = {"fuzer_id": r["fuzer_id"], "planted": f"{r['key']} written {v}",
                                  "option": f"tag {r['key']}"}
    # a tag that isn't there: a loop number near real ones, so the options look plausible and the answer is "none"
    known = {r["key"] for r in conn.execute("select key from entity_key where kind = 'tag'")}
    base = rows[0]["key"] if rows else "QQ-Z-9999"
    loop = re.search(r"(\d+)(?=\D*$)", base)  # the last number in the key: the loop
    shifted = (base[:loop.start()] + str(int(loop[1]) + n).zfill(len(loop[1])) + base[loop.end():]
               for n in range(7, 400, 7)) if loop else iter(())
    decoy = next((d for d in shifted if d not in known), "QQ-Z-9999")
    exceptions.raise_exception(conn, TYPE, "decoy", f"Reading out of range on {decoy}", "eval", source="eval",
                               refs=[decoy])
    key[f"unresolved|{decoy}"] = {"fuzer_id": None, "planted": "names nothing"}

    usual = conn.execute(
        "select value #>> '{}' as v from golden where field = 'manufacturer' and rule <> 'derived' "
        "and length(value #>> '{}') >= 6 group by 1 having count(*) >= 2 order by count(*) desc, 1 limit 1").fetchone()
    if usual:
        usual = usual["v"]
        typo = usual[:-2] + usual[-1]  # a letter dropped
        g = conn.execute(
            "select g.fuzer_id, c.source, c.record_type, c.source_key, r.payload from golden g join crosswalk c on "
            "c.fuzer_id = g.fuzer_id and c.source = g.source join source_record r on (r.source, r.record_type, "
            "r.source_key) = (c.source, c.record_type, c.source_key) where g.field = 'manufacturer' and "
            "g.value #>> '{}' = %s order by g.fuzer_id limit 1", (usual,)).fetchone()
        payload = {k: (typo if v == usual else v) for k, v in g["payload"].items()}
        conn.execute("update source_record set payload = %s where (source, record_type, source_key) = (%s, %s, %s)",
                     (Jsonb(payload), g["source"], g["record_type"], g["source_key"]))
        conn.execute("update golden set value = %s where fuzer_id = %s and field = 'manufacturer'",
                     (Jsonb(typo), g["fuzer_id"]))
        a, b = sorted((usual, typo))
        key[f"spelling|manufacturer|{a}|{b}"] = {"keep": usual, "planted": f"{usual} written {typo}",
                                                 "option": f"keep {usual!r}"}
    conn.commit()
    return key


def _right(p: dict, truth: dict) -> bool:
    a = p["answer"] or {}
    opt = next((o for o in p["question"]["options"] if o["id"] == a.get("choice")), None)
    if "fuzer_id" in truth:
        return (opt or {}).get("fuzer_id") == truth["fuzer_id"]
    return (opt or {}).get("keep") == truth["keep"]


def score(conn, key: dict) -> dict:
    rows = {f"{r['kind']}|{r['subject']}": dict(r) for r in conn.execute(
        "select kind, subject, question, answer from steward_proposal where status = 'open'")}
    found = [(k, rows.get(k)) for k in key]
    asked = [(k, p) for k, p in found if p is not None]
    answered = [(k, p) for k, p in asked if p["answer"] and p["answer"]["choice"] != "none"]
    right = [(k, p) for k, p in answered if _right(p, key[k])]
    nones = [k for k, p in asked if key[k].get("fuzer_id", "x") is None]
    by_threshold = {}
    for t in THRESHOLDS:
        at = [(k, p) for k, p in answered if p["answer"]["confidence"] >= t]
        by_threshold[str(t)] = {"proposals": len(at), "right": sum(1 for k, p in at if _right(p, key[k])),
                                "precision": round(sum(1 for k, p in at if _right(p, key[k])) / len(at), 3)
                                if at else None}
    should = [k for k in key if key[k].get("fuzer_id", "x") is not None]
    return {"planted": len(key), "asked": len(asked), "answered": len(answered), "right": len(right),
            "precision": round(len(right) / len(answered), 3) if answered else None,
            "recall": round(sum(1 for k, _ in right if k in should) / len(should), 3) if should else None,
            "declined_correctly": sum(1 for k in nones if (rows[k]["answer"] or {}).get("choice", "none") == "none"),
            "by_threshold": by_threshold,
            "misses": [{"question": k, "planted": key[k]["planted"], "answer": (p or {}).get("answer")}
                       for k, p in found if p is None or not _right(p, key[k]) and key[k].get("fuzer_id", "x") is not None]}


class scripted:
    """A stand-in for Claude that answers planted questions from the key (and "none" otherwise), for CI."""

    def __init__(self, key: dict, wrong: int = 0):
        self.key, self.wrong, self.calls = key, wrong, 0
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls += 1
        asked = json.loads(kw["messages"][0]["content"])["questions"]
        answers = []
        for q in asked:
            c = q["context"]
            subject = (f"unresolved|{c['value']}" if q["kind"] == "unresolved" else
                       f"spelling|{c['field']}|" + "|".join(sorted(v["value"] for v in c["values"]))
                       if q["kind"] == "spelling" else None)
            truth = self.key.get(subject) or {}
            choice = next((o["id"] for o in q["options"] if truth.get("option") and
                           (o["text"] == truth["option"] or o["text"].startswith(truth["option"] + ":")
                            or o["text"].endswith(truth["option"]))), "none")
            if choice != "none" and self.wrong > 0:
                self.wrong -= 1
                choice = next(o["id"] for o in q["options"] if o["id"] != choice)
            answers.append({"id": q["id"], "choice": choice, "confidence": 0.95 if choice != "none" else 0.2,
                            "reason": "scripted", "evidence": []})
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                                                             cache_read_input_tokens=0),
                               content=[SimpleNamespace(type="text", text=json.dumps({"answers": answers}))])


def run(conn, client=None, scripted_wrong: int | None = None) -> dict:
    """Plant, propose, score. `scripted_wrong` (a number) answers from the key instead of Claude, that many wrong."""
    key = plant(conn)
    if scripted_wrong is not None:
        client = scripted(key, scripted_wrong)
    stats = proposals.propose(conn, client=client, kinds=("unresolved", "spelling"), limit=None, force=True)
    return {"propose": {k: stats.get(k) for k in ("questions", "asked", "answered", "failed_requests", "cost_usd")},
            **score(conn, key), "key": key}


if __name__ == "__main__":
    from ..core import db

    with db.connect() as c:
        print(json.dumps(run(c), indent=2, default=str))
