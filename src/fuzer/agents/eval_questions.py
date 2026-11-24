"""The eval questions people keep for a skill (Ops IQ, Evals).

A skill's questions are its pack's answer keys for this project's bundle (shipped, in the bundle's
evals/<eval>.yaml), the ones people added, and those drawn from the cases behind a published version. A shipped key
can't be edited where it ships, so editing or retiring one keeps an override (eval_question.shipped names it). Each
question has a `key` the page acts on: `q:<id>` for a kept row, `shipped:<n>` for a shipped key nobody changed.

    for_skill(conn, skill)                       every question with where it came from and whether it is asked
    active(conn, skill)                          the answer keys the eval gate and a skill's eval run ask
    save(conn, skill, key, question, must_mention, specialists, by, note)   add (key None) or edit one
    retire(conn, skill, key, by) / restore(...)  stop or start asking one
"""

from psycopg.types.json import Jsonb

from . import packs, skill_versions


def shipped(conn, skill: str) -> list[dict]:
    """The pack's answer keys for this project's bundle; [] for a skill with no pack or a database with no project."""
    from ..core.pack import resolve

    skill_versions._skill(skill)
    if skill not in packs.shipped():
        return []
    try:
        bundle = resolve(conn).name
    except Exception:  # a database with no project: no bundle, so none of the pack's answer keys apply
        conn.rollback()
        return []
    return list((packs.load(skill).get("questions") or {}).get(bundle) or [])


def _key(q: dict) -> dict:
    return {k: q[k] for k in ("question", "must_mention", "specialists", "charts") if q.get(k)}


def for_skill(conn, skill: str) -> list[dict]:
    rows = conn.execute("select * from eval_question where skill = %s order by id", (skill,)).fetchall()
    over = {r["shipped"]: r for r in rows if r["shipped"]}
    out = []
    for n, q in enumerate(shipped(conn, skill)):
        r = over.get(q["question"])
        base = {"origin": "shipped", "shipped": q["question"], "charts": q.get("charts") or []}
        if r:
            out.append({**base, **_row(r), "edited": r["question"] != q["question"]
                        or r["must_mention"] != (q.get("must_mention") or []) or r["specialists"] != (q.get("specialists") or [])})
        else:
            out.append({**base, "key": f"shipped:{n}", "question": q["question"],
                        "must_mention": q.get("must_mention") or [], "specialists": q.get("specialists") or [],
                        "status": "active", "edited": False, "by": None, "at": None, "note": None})
    out += [{"origin": "added", "shipped": None, "charts": [], "edited": False, **_row(r)} for r in rows if not r["shipped"]]
    seen = {q["question"] for q in out}
    for v in skill_versions.history(conn, skill):
        if v.get("published_at") and v["status"] in ("published", "retired"):
            for q in v.get("questions") or []:
                if q.get("question") and q["question"] not in seen:
                    seen.add(q["question"])
                    out.append({"origin": "case", "key": None, "shipped": None, "question": q["question"],
                                "must_mention": q.get("must_mention") or [], "specialists": q.get("specialists") or [],
                                "charts": [], "status": "active", "edited": False, "version": v["version"],
                                "by": v.get("published_by"), "at": v.get("published_at"), "note": None})
    return out


def _row(r) -> dict:
    return {"key": f"q:{r['id']}", "question": r["question"], "must_mention": r["must_mention"],
            "specialists": r["specialists"], "status": r["status"], "note": r["note"],
            "by": r["updated_by"] or r["created_by"], "at": r["updated_at"].isoformat()}


def active(conn, skill: str) -> list[dict]:
    """The answer keys asked: shipped and added ones that are active (cases' questions are added by the gate)."""
    return [_key(q) for q in for_skill(conn, skill) if q["status"] == "active" and q["origin"] != "case"]


def _clean(must_mention) -> list:
    out = []
    for m in must_mention or []:
        if isinstance(m, list):
            terms = [str(t).strip() for t in m if str(t).strip()]
            if terms:
                out.append(terms if len(terms) > 1 else terms[0])
        elif str(m).strip():
            out.append(str(m).strip())
    return out


def _shipped_for(conn, skill: str, key: str) -> dict:
    try:
        return shipped(conn, skill)[int(key.split(":", 1)[1])]
    except (ValueError, IndexError):
        raise ValueError(f"{skill} has no shipped question {key}") from None


def _id(skill: str, conn, key: str) -> int:
    try:
        qid = int(key.split(":", 1)[1])
    except (ValueError, IndexError):
        raise ValueError(f"no question {key}") from None
    if conn.execute("select 1 from eval_question where id = %s and skill = %s", (qid, skill)).fetchone() is None:
        raise ValueError(f"{skill} has no question {key}")
    return qid


def save(conn, skill: str, key: str | None, question: str, must_mention: list, specialists: list, by: str,
         note: str | None = None) -> dict:
    """Add a question (key None), edit a kept one (q:<id>) or override a shipped one (shipped:<n>). A question needs
    its wording and at least one fact a good answer names."""
    skill_versions._skill(skill)
    question, must = (question or "").strip(), _clean(must_mention)
    if not question:
        raise ValueError("a question needs its wording")
    if not must:
        raise ValueError("say at least one fact a good answer names")
    specialists = [s.strip() for s in specialists or [] if s and s.strip()]
    if key and key.startswith("shipped:"):
        q = _shipped_for(conn, skill, key)
        row = conn.execute(
            "insert into eval_question (skill, shipped, question, must_mention, specialists, note, created_by) "
            "values (%s, %s, %s, %s, %s, %s, %s) on conflict (skill, shipped) where shipped is not null do update set "
            "question = excluded.question, must_mention = excluded.must_mention, specialists = excluded.specialists, "
            "note = excluded.note, updated_by = %s, updated_at = now() returning *",
            (skill, q["question"], question, Jsonb(must), Jsonb(specialists), note, by, by)).fetchone()
    elif key:
        row = conn.execute(
            "update eval_question set question = %s, must_mention = %s, specialists = %s, note = coalesce(%s, note), "
            "updated_by = %s, updated_at = now() where id = %s returning *",
            (question, Jsonb(must), Jsonb(specialists), note, by, _id(skill, conn, key))).fetchone()
    else:
        row = conn.execute(
            "insert into eval_question (skill, question, must_mention, specialists, note, created_by) "
            "values (%s, %s, %s, %s, %s, %s) returning *",
            (skill, question, Jsonb(must), Jsonb(specialists), note, by)).fetchone()
    conn.commit()
    return _kept(conn, skill, row)


def _kept(conn, skill: str, row) -> dict:
    """The question as for_skill shows it (where it came from, whether it was edited)."""
    key = f"q:{row['id']}"
    return next((q for q in for_skill(conn, skill) if q["key"] == key), _row(row))


def _status(conn, skill: str, key: str, by: str, status: str) -> dict:
    skill_versions._skill(skill)
    if key.startswith("shipped:"):
        q = _shipped_for(conn, skill, key)
        row = conn.execute(
            "insert into eval_question (skill, shipped, question, must_mention, specialists, status, created_by) "
            "values (%s, %s, %s, %s, %s, %s, %s) on conflict (skill, shipped) where shipped is not null do update set "
            "status = excluded.status, updated_by = %s, updated_at = now() returning *",
            (skill, q["question"], q["question"], Jsonb(q.get("must_mention") or []), Jsonb(q.get("specialists") or []),
             status, by, by)).fetchone()
    else:
        row = conn.execute("update eval_question set status = %s, updated_by = %s, updated_at = now() "
                           "where id = %s returning *", (status, by, _id(skill, conn, key))).fetchone()
    conn.commit()
    return _kept(conn, skill, row)


def retire(conn, skill: str, key: str, by: str) -> dict:
    return _status(conn, skill, key, by, "retired")


def restore(conn, skill: str, key: str, by: str) -> dict:
    return _status(conn, skill, key, by, "active")
