"""Ops IQ, Evals: the questions people keep for a skill, a skill's eval run with its answers scored, the checks on what
the agents wrote, and the model's grade, which waits and says why while no model may be called."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.agents import eval_questions, eval_runs, grader, output_checks
from fuzer.api import evals as evals_api

pytestmark = pytest.mark.integration
SKILL = "maintenance-integrity/compression"


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


@pytest.fixture(autouse=True)
def fresh(conn):
    conn.execute("delete from eval_question; delete from ai_eval_run; delete from skill_version; delete from entity_summary")
    conn.commit()


class FakeGrader:
    """A stand-in model: it gives every item a 4, except an answer that never mentions the strainer, a 2."""
    def __init__(self):
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        items = json.loads(kw["messages"][0]["content"])
        grades = [{"index": it["index"], "score": 2 if "answer" in it and "strainer" not in it["answer"] else 4,
                   "why": "Names the strainer." if "strainer" in json.dumps(it) else "Misses the strainer."} for it in items]
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps({"grades": grades}))],
                               usage=SimpleNamespace(input_tokens=800, output_tokens=90, cache_read_input_tokens=0))


def _answer(question, skill, playbook):
    return {"answer": "Check the suction strainer differential pressure.", "specialists": ["maintenance-integrity"],
            "cost_usd": 0.01}


def test_a_skills_questions_are_its_shipped_keys_with_what_people_added_edited_and_retired(conn):
    qs = eval_questions.for_skill(conn, SKILL)
    shipped = [q for q in qs if q["origin"] == "shipped"]
    assert shipped and all(q["key"].startswith("shipped:") and not q["edited"] for q in shipped)
    n = len(eval_questions.active(conn, SKILL))

    added = eval_questions.save(conn, SKILL, None, "Why is K-1 surging?", ["strainer", ["suction", "inlet"]],
                                ["maintenance-integrity"], "olga", None)
    assert added["key"].startswith("q:") and added["origin"] == "added"
    edited = eval_questions.save(conn, SKILL, shipped[0]["key"], shipped[0]["question"] + " Name the tag.",
                                 shipped[0]["must_mention"], shipped[0]["specialists"], "olga", "sharper")
    assert edited["edited"] and edited["key"].startswith("q:")  # an override kept beside the shipped key
    active = eval_questions.active(conn, SKILL)
    assert len(active) == n + 1 and {"question": "Why is K-1 surging?", "must_mention": ["strainer", ["suction", "inlet"]],
                                     "specialists": ["maintenance-integrity"]} in active
    assert shipped[0]["question"] not in [q["question"] for q in active]

    eval_questions.retire(conn, SKILL, added["key"], "olga")
    assert len(eval_questions.active(conn, SKILL)) == n
    eval_questions.restore(conn, SKILL, added["key"], "olga")
    assert len(eval_questions.active(conn, SKILL)) == n + 1
    with pytest.raises(ValueError):
        eval_questions.save(conn, SKILL, None, "  ", [], [], "olga", None)


def test_a_skill_run_asks_its_questions_scores_each_answer_and_is_graded(conn, monkeypatch):
    monkeypatch.setattr(eval_runs, "_available", lambda: None)
    eval_questions.save(conn, SKILL, None, "Why is K-1 surging?", ["strainer"], ["maintenance-integrity"], "olga", None)
    run = eval_runs.start_skill(conn, SKILL, "olga")
    assert run["status"] == "running" and run["grade_status"] == "waiting"
    eval_runs.ask_skill(run["id"], answer=_answer, grade_client=FakeGrader())
    got = eval_runs.get(conn, str(run["id"]))
    assert got["status"] == "done" and got["results"] and got["grade_status"] == "graded"
    k1 = next(r for r in got["results"] if r["question"] == "Why is K-1 surging?")
    assert k1["mentions"] == 1 and k1["routed"] and k1["grade"]["score"] == 4 and "strainer" in k1["answer"]
    assert 0 <= got["score"] <= 1 and got["grade"] == 0.75 and got["cost_usd"] > 0
    hist = eval_runs.history(conn, "skill:" + SKILL)
    assert hist["runs"][0]["id"] == run["id"] and hist["questions"]
    over = {s["subject"]: s for s in eval_runs.overview(conn)["subjects"]}
    assert over["skill:" + SKILL]["latest"]["id"] == run["id"] and "outputs:daily_report" in over


def test_without_a_model_a_skill_run_is_refused_before_spending(conn, monkeypatch):
    monkeypatch.setattr(eval_runs.llm, "availability", lambda scope: {"available": False, "why": "AI is paused."})
    with pytest.raises(eval_runs.Unavailable, match="AI is paused"):
        eval_runs.start_skill(conn, SKILL, "olga")
    assert conn.execute("select count(*) as n from ai_eval_run").fetchone()["n"] == 0


def _summary(conn, headline, points, facts):
    from psycopg.types.json import Jsonb

    fid = conn.execute("select fuzer_id from entity_label where label = 'OKAN-20'").fetchone()["fuzer_id"]
    conn.execute("insert into entity_summary (fuzer_id, headline, points, facts) values (%s, %s, %s, %s)",
                 (fid, headline, Jsonb(points), Jsonb({"id": fid, **facts})))
    conn.commit()
    return fid


def test_an_entity_summary_is_checked_against_its_facts():
    facts = {"id": "e1", "open_work": {"total": 12, "top": [{"case": 77, "title": "Seal leak"}]}}
    good = output_checks.check_entity_summary({"headline": "12 open, one seal leak", "facts": facts,
                                               "points": [{"text": "Case 77 is a seal leak.", "cites": ["case:77"]}]})
    assert good["ok"], good
    bad = output_checks.check_entity_summary({"headline": "40 open", "facts": facts, "points": [
        {"text": "Open the 20-XV-3100 valve now.", "cites": ["case:99"]}, {"text": "Uncited.", "cites": []}]})
    assert not bad["ok"] and bad["unknown_numbers"] == ["40"] and bad["unknown_citations"] == ["case:99"]
    assert bad["uncited_points"] == ["Uncited."] and bad["operating_instructions"]


def test_output_checks_run_without_a_model_and_the_grade_waits_with_why(conn, monkeypatch):
    _summary(conn, "12 open", [{"text": "Twelve open cases.", "cites": []}], {"open_work": {"total": 12}})
    monkeypatch.setattr(eval_runs.llm, "availability", lambda scope: {"available": False, "why": "AI is paused."})
    run = eval_runs.run_outputs(conn, "outputs:entity_summary", "olga")
    assert run["status"] == "done" and run["scores"]["items"] == 1 and run["scores"]["passed"] == 0
    waited = eval_runs.grade_run(run["id"], conn=conn)
    assert waited["grade_status"] == "waiting" and waited["grade_why"] == "AI is paused."
    assert waited["results"][0]["checks"]["uncited_points"] and "grade_input" not in waited["results"][0]
    graded = eval_runs.grade_run(run["id"], client=FakeGrader(), conn=conn)  # later, when a model may be called
    assert graded["grade_status"] == "graded" and graded["results"][0]["grade"]["score"] == 4


def test_the_grader_sends_nothing_without_a_model(monkeypatch):
    monkeypatch.setattr(grader.llm, "availability", lambda scope: {"available": False, "why": "No key."})
    with pytest.raises(grader.Unavailable, match="No key"):
        grader.grade("summary", [{"facts": {}, "summary": "x"}])
    assert grader.grade("summary", [])["score"] is None


def test_the_api_reads_runs_and_keeps_questions(conn, monkeypatch):
    app = FastAPI()
    app.include_router(evals_api.router)
    app.dependency_overrides[evals_api.get_conn] = lambda: conn
    monkeypatch.setattr(eval_runs.llm, "availability", lambda scope: {"available": False, "why": "AI is paused."})
    c = TestClient(app)
    assert c.get("/evals").json()["ai"] == {"available": False, "why": "AI is paused."}
    r = c.put("/evals/questions", json={"skill": SKILL, "question": "Which train trips most?", "must_mention": ["K-1"]})
    assert r.status_code == 200, r.text
    detail = c.get("/evals/subject", params={"subject": "skill:" + SKILL}).json()
    assert any(q["question"] == "Which train trips most?" for q in detail["questions"])
    assert c.post("/evals/skills/run", json={"skill": SKILL}).status_code == 503
    assert c.get("/evals/subject", params={"subject": "nothing"}).status_code == 409
    run = c.post("/evals/outputs/run", json={"subject": "outputs:daily_report"})
    assert run.status_code == 200 and run.json()["status"] == "done"
    assert c.get(f"/evals/runs/{run.json()['id']}").status_code == 200
    assert c.get("/evals/runs/999999").status_code == 404
