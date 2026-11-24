"""AI eval runs (Ops IQ, Evals): how well each skill answers its questions, and how sound the summaries, report
notes and daily reports Fuzer kept are, run by a person and kept with their history.

Two kinds of subject:

- **skill:<path>**: the skill's active eval questions (agents.eval_questions) asked of its live version through the
  operations assistant, each answer scored as the eval gate scores it (facts named, the right specialist; agents.
  skill_gate.score_answer). Asking costs model calls, so a run needs a model now and runs after the request returns.
- **outputs:<kind>**: the checks on the latest entity summaries, report notes or daily reports (agents.output_checks).
  They read what was kept and call nothing, so they run at once.

Either way the model then grades the results (agents.grader) when a model may be called; otherwise the run keeps
`grade_status` waiting with why, and someone grades it later. Runs recorded with the code (`fuzer eval --agents` or
`--packs --record`, evals/runs/) show in a skill's history beside the runs made here.

    overview(conn)                       every subject with its latest run, trend and questions
    history(conn, subject)               a subject's runs, newest first, recorded ones included
    get(conn, run_id)                    one run with each result
    start_skill(conn, skill, by)         a skill run, waiting to ask; raises Unavailable without a model
    ask_skill(run_id, answer=None)       ask and score on its own connection, then grade
    run_outputs(conn, subject, by, grade)  the checks now; graded after when `grade`
    grade_run(run_id, client=None)       grade a run that waits, on its own connection
"""

import json
import logging
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from ..core import db, llm
from ..resources import ROOT
from . import eval_questions, grader, output_checks, skill_gate, skill_versions
from .capabilities import catalog

log = logging.getLogger(__name__)
RECORDED = ROOT / "evals" / "runs"
SKILL = "skill:"
OPS_IQ = "orchestrator/answering"  # the assistant eval (all its specialists) is recorded against Ops IQ's own skill
TREND = 8


class Unavailable(RuntimeError):
    """A run that needs a model when none may be called, or a skill with nothing to ask."""


def _label(subject: str) -> str:
    if subject in output_checks.SUBJECTS:
        return output_checks.SUBJECTS[subject]["label"]
    path = subject.removeprefix(SKILL)
    from . import skill_book

    s = skill_versions._skill(path)
    return "Ops IQ answers" if path == OPS_IQ else skill_book._title(s)


def _subject(subject: str) -> str:
    if subject in output_checks.SUBJECTS:
        return subject
    if subject.startswith(SKILL):
        skill_versions._skill(subject.removeprefix(SKILL))
        return subject
    raise ValueError(f"no eval subject {subject!r}")


def _row(r, results: bool = False) -> dict:
    out = {"id": r["id"], "origin": "run", "subject": r["subject"], "status": r["status"], "by": r["by_user"],
           "at": r["started_at"].isoformat(), "finished_at": r["finished_at"] and r["finished_at"].isoformat(),
           "scores": r["scores"], "score": (r["scores"] or {}).get("score"), "grade_status": r["grade_status"],
           "grade": (r["grade"] or {}).get("score"), "grade_why": r["grade_why"], "error": r["error"],
           "cost_usd": float(r["cost_usd"] or 0)}
    if results:
        grades = (r["grade"] or {}).get("items") or []
        out["results"] = [{**{k: v for k, v in x.items() if k != "grade_input"}, "grade": grades[i] if i < len(grades) else None}
                          for i, x in enumerate(r["results"] or [])]
        out["grade_model"] = (r["grade"] or {}).get("model")
    return out


# ---- recorded with the code


def _bundle(conn) -> str | None:
    from ..core.pack import resolve

    try:
        return resolve(conn).name
    except Exception:
        conn.rollback()
        return None


def _score(results: list[dict]) -> float | None:
    if not results:
        return None
    return round(sum((r.get("mentions") or 0) * (1 if r.get("routed") else 0.5) for r in results) / len(results), 3)


def recorded(conn) -> dict[str, list[dict]]:
    """The runs recorded with the code for this project's bundle, by subject: the assistant eval against Ops IQ's
    skill, each pack eval against its skill."""
    bundle, out = _bundle(conn), {}
    if not RECORDED.is_dir():
        return out
    for p in sorted(RECORDED.glob("*.json")):
        try:
            run = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        model = run.get("model")
        if run.get("kind") == "assistant":
            parts = {OPS_IQ: run}
        elif run.get("kind") == "packs" and run.get("bundle") == bundle:
            parts = run.get("packs") or {}
        else:
            continue
        for path, part in parts.items():
            results = part.get("results") or []
            out.setdefault(SKILL + path, []).append({
                "id": f"recorded:{p.stem}:{path}", "origin": "recorded", "subject": SKILL + path, "status": "done",
                "by": model and f"recorded with the code ({model})", "at": f"{run['date']}T00:00:00+00:00",
                "finished_at": None, "score": _score(results), "grade_status": "not_graded", "grade": None,
                "grade_why": None, "error": None, "cost_usd": part.get("cost_usd") or 0,
                "scores": {"score": _score(results), "questions": part.get("questions") or len(results),
                           "mentions": part.get("mentions"), "routed": part.get("routed"), "cited": part.get("cited"),
                           "safe": part.get("safe")},
                "results": [{"question": r.get("question"), "mentions": r.get("mentions"), "missing": r.get("missing"),
                             "routed": r.get("routed"), "specialists": r.get("specialists"), "cost_usd": r.get("cost_usd"),
                             "grade": None} for r in results]})
    return out


# ---- reading


def overview(conn) -> dict:
    """Every subject: the outputs first, then Ops IQ and each skill, with its latest run, its trend and its questions."""
    from . import skill_book

    rows = conn.execute("select * from (select r.*, row_number() over (partition by subject order by started_at desc) "
                        "as n from ai_eval_run r) x where n <= %s order by subject, started_at desc", (TREND,)).fetchall()
    runs: dict[str, list[dict]] = {}
    for r in rows:
        runs.setdefault(r["subject"], []).append(_row(r))
    for subject, xs in recorded(conn).items():
        runs[subject] = sorted(runs.get(subject, []) + xs, key=lambda x: x["at"], reverse=True)[:TREND]
    subjects = [{"subject": s, "label": v["label"], "kind": "outputs", "group": "What the agents wrote",
                 "questions": None} for s, v in output_checks.SUBJECTS.items()]
    for s in sorted(catalog.SKILL_CATALOG, key=lambda s: (s.path != OPS_IQ, s.path)):
        if s.shared:
            continue
        subjects.append({"subject": SKILL + s.path, "label": _label(SKILL + s.path), "kind": "skill",
                         "group": skill_book._label(s.owner) if s.path != OPS_IQ else "Ops IQ",
                         "questions": len(eval_questions.active(conn, s.path))})
    for x in subjects:
        xs = runs.get(x["subject"], [])
        x["latest"] = xs[0] if xs else None
        x["trend"] = [r["score"] for r in reversed(xs) if r["score"] is not None]
    conn.commit()
    return {"subjects": subjects, "ai": llm.availability("agents")}


def history(conn, subject: str) -> dict:
    subject = _subject(subject)
    runs = [_row(r) for r in conn.execute(
        "select * from ai_eval_run where subject = %s order by started_at desc limit 100", (subject,))]
    runs = sorted(runs + recorded(conn).get(subject, []), key=lambda x: x["at"], reverse=True)
    skill = subject.removeprefix(SKILL) if subject.startswith(SKILL) else None
    out = {"subject": subject, "label": _label(subject), "kind": "skill" if skill else "outputs", "runs": runs,
           "questions": eval_questions.for_skill(conn, skill) if skill else None, "ai": llm.availability("agents")}
    conn.commit()
    return out


def get(conn, run_id: str) -> dict | None:
    if run_id.startswith("recorded:"):
        return next((r for xs in recorded(conn).values() for r in xs if r["id"] == run_id), None)
    try:
        r = conn.execute("select * from ai_eval_run where id = %s", (int(run_id),)).fetchone()
    except ValueError:
        return None
    return {**_row(r, results=True), "label": _label(r["subject"])} if r else None


# ---- running


def _available() -> None:
    if not (ai := llm.availability("agents"))["available"]:
        raise Unavailable(ai["why"])


def start_skill(conn, skill: str, by: str) -> dict:
    """A run of a skill's eval, waiting to ask: refused without a model, or when the skill has no questions."""
    skill_versions._skill(skill)
    live = skill_versions.live(conn, skill)
    qs = skill_gate.questions(conn, skill, live["version"])
    if not qs:
        raise Unavailable("This skill has no eval questions yet. Add one before running its eval.")
    _available()
    r = conn.execute("insert into ai_eval_run (subject, by_user, scores) values (%s, %s, %s) returning *",
                     (SKILL + skill, by, Jsonb({"questions": len(qs), "asked": 0, "version": live["version"]}))).fetchone()
    conn.commit()
    return _row(r)


def ask_skill(run_id: int, answer=None, grade_client=None) -> None:
    """Ask a waiting skill run's questions of the live version, score each answer, then grade them. On its own
    connection: it runs after the request that started it has returned."""
    with db.connect() as conn:
        r = conn.execute("select * from ai_eval_run where id = %s", (run_id,)).fetchone()
        skill = r["subject"].removeprefix(SKILL)
        live = skill_versions.live(conn, skill)
        qs = skill_gate.questions(conn, skill, live["version"])
        conn.commit()
        ask = answer or skill_gate._ask
        results = []
        try:
            for q in qs:
                results.append(skill_gate.score_answer(q, ask(q["question"], skill, live["playbook"])))
                conn.execute("update ai_eval_run set results = %s, scores = scores || %s where id = %s",
                             (Jsonb(results), Jsonb({"asked": len(results)}), run_id))
                conn.commit()
        except Exception as e:
            log.warning("eval run %s failed", run_id, exc_info=True)
            conn.rollback()
            conn.execute("update ai_eval_run set status = 'failed', error = %s, finished_at = now(), results = %s "
                         "where id = %s", (str(e)[:500], Jsonb(results), run_id))
            conn.commit()
            return
        n = len(results) or 1
        scores = {"score": _score(results), "questions": len(results), "asked": len(results), "version": live["version"],
                  "mentions": round(sum(x["mentions"] or 0 for x in results) / n, 3),
                  "routed": round(sum(x["routed"] for x in results) / n, 3)}
        cost = round(sum(x["cost_usd"] or 0 for x in results), 4)
        conn.execute("update ai_eval_run set status = 'done', scores = %s, results = %s, cost_usd = %s, "
                     "finished_at = now() where id = %s", (Jsonb(scores), Jsonb(results), cost, run_id))
        conn.commit()
        _grade(conn, run_id, grade_client)


def run_outputs(conn, subject: str, by: str, n: int = 20) -> dict:
    """The checks on the latest kept outputs of one kind, now; grading waits for grade_run."""
    if subject not in output_checks.SUBJECTS:
        raise ValueError(f"no outputs subject {subject!r}")
    items = output_checks.SUBJECTS[subject]["read"](conn, n)
    results = [{k: v for k, v in it.items()} for it in items]
    checks = [it["checks"] for it in items]
    total = len(items) or 1
    scores = {"score": round(sum(c["ok"] for c in checks) / total, 3) if items else None, "items": len(items),
              "passed": sum(c["ok"] for c in checks),
              "uncited": sum(bool(c.get("uncited_points") or c.get("uncited_lines")) for c in checks),
              "unknown_citations": sum(bool(c.get("unknown_citations")) for c in checks),
              "unknown_numbers": sum(bool(c.get("unknown_numbers")) for c in checks),
              "operating_instructions": sum(bool(c.get("operating_instructions")) for c in checks)}
    r = conn.execute(
        "insert into ai_eval_run (subject, status, scores, results, by_user, finished_at, grade_status, grade_why) "
        "values (%s, 'done', %s, %s, %s, now(), 'waiting', %s) returning *",
        (subject, Jsonb(scores), Jsonb(results), by, None if items else "Nothing to grade.")).fetchone()
    conn.commit()
    return _row(r)


def _items(subject: str, results: list[dict]) -> tuple[str, list[dict]]:
    if subject in output_checks.SUBJECTS:
        return output_checks.SUBJECTS[subject]["rubric"], [x.get("grade_input") or {"text": x.get("text")}
                                                           for x in results]
    return "answer", [{"question": x["question"], "answer_key": x.get("must_mention"), "answer": x.get("answer") or ""}
                      for x in results]


def _grade(conn, run_id: int, client=None) -> dict:
    r = conn.execute("select * from ai_eval_run where id = %s for update", (run_id,)).fetchone()
    if r["status"] != "done" or not r["results"]:
        conn.rollback()
        return _row(r)
    kind, items = _items(r["subject"], r["results"])
    try:
        if client is None:
            _available()
        conn.execute("update ai_eval_run set grade_status = 'grading', grade_why = null where id = %s", (run_id,))
        conn.commit()
        got = grader.grade(kind, items, client=client, conn=conn)
    except (Unavailable, grader.Unavailable) as e:
        conn.rollback()
        conn.execute("update ai_eval_run set grade_status = 'waiting', grade_why = %s where id = %s", (str(e), run_id))
        conn.commit()
        return get(conn, str(run_id))
    except Exception as e:
        log.warning("grading eval run %s failed", run_id, exc_info=True)
        conn.rollback()
        conn.execute("update ai_eval_run set grade_status = 'failed', grade_why = %s where id = %s",
                     (str(e)[:500], run_id))
        conn.commit()
        return get(conn, str(run_id))
    conn.execute("update ai_eval_run set grade_status = 'graded', grade = %s, grade_why = null, "
                 "cost_usd = cost_usd + %s where id = %s",
                 (Jsonb({"score": got["score"], "items": got["items"], "model": got["usage"].get("model"),
                         "graded_at": datetime.now(UTC).isoformat()}),
                  got["usage"].get("cost_usd") or 0, run_id))
    conn.commit()
    return get(conn, str(run_id))


def grade_run(run_id: int, client=None, conn=None) -> dict:
    """Grade one run now (a waiting or failed grade, or a new one); without a model it stays waiting with why."""
    if conn is not None:
        return _grade(conn, run_id, client)
    with db.connect() as c:
        return _grade(c, run_id, client)


def waiting(conn) -> list[int]:
    return [r["id"] for r in conn.execute(
        "select id from ai_eval_run where status = 'done' and grade_status in ('waiting', 'failed') "
        "and results <> '[]'::jsonb order by started_at")]
