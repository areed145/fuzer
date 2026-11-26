"""Plan rules (P5.23a, ops/plan_rules.py): a rule typed in words on Plan is read without a model into a typed rule,
said back, switched off and on, and marks each job on the Okan plan that breaks it."""

from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from fuzer.core.plan import PlanError
from fuzer.ops import plan, plan_rules, schedule

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "fakeplant" / "bundles" / "okan" / "schedules" / "OK-TAR-2026.yaml"


@pytest.fixture(scope="module")
def c(okan_db):
    conn = okan_db.conn
    plan.sync_work_orders(conn)
    schedule.import_template(conn, yaml.safe_load(TEMPLATE.read_text()), "tests")
    conn.commit()
    yield conn
    conn.execute("delete from plan_rule")
    conn.commit()


def _act(c, code: str) -> dict:
    return c.execute("select * from plan_item where project = 'OK-TAR-2026' and code = %s", (code,)).fetchone()


def _window(c) -> dict[int, list[dict]]:
    """Plan's timeline over the turnaround, as the page asks for it: per job the rules it breaks."""
    span = c.execute("select min(start_at) a, max(end_at) b from plan_item where project = 'OK-TAR-2026'").fetchone()
    shown = plan.items(c, span["a"], span["b"], active=True, rules=True)
    return {i["id"]: i["breaks"] for i in shown}


def test_a_rule_typed_in_words_flags_the_tiles_that_break_it_on_the_okan_plan(c):
    """A deadline the box-up of 20-K-2200B misses: its bar (and only the jobs the rule names) is marked, the rule is said
    back and lists the job; switched off it marks nothing, and on again it does."""
    a0130 = _act(c, "A0130")
    day = (a0130["end_at"] - timedelta(days=1)).strftime("%d %b")
    out = plan_rules.add(c, f"A0130 done by {day}", "user:pat")
    c.commit()
    [r] = [r for r in out if r["form"] == "deadline"]
    assert r["on"] and r["say"].startswith("A0130 done by ")
    assert [b["item_id"] for b in r["breaks"]] == [a0130["id"]]
    assert r["breaks"][0]["say"].startswith("finishes ")
    marks = _window(c)
    assert marks[a0130["id"]] == [{"rule": r["id"], "rule_say": r["say"], "say": r["breaks"][0]["say"]}]
    assert not marks[_act(c, "A0110")["id"]], "a job the rule doesn't name is not marked"
    plan_rules.switch(c, r["id"], False, "user:pat")
    c.commit()
    assert not _window(c)[a0130["id"]]
    [off] = [x for x in plan_rules.rules(c) if x["id"] == r["id"]]
    assert not off["on"] and off["breaks"] == [] and off["say"] == r["say"]
    plan_rules.switch(c, r["id"], True, "user:pat")
    c.commit()
    assert _window(c)[a0130["id"]]
    plan_rules.remove(c, r["id"], "user:pat")
    c.commit()
    assert not _window(c)[a0130["id"]]


def test_each_form_is_read_and_checked(c):
    """Duration, gap, overlap and sequence, each on the turnaround as levelled: the bearings take 48 h, and the
    impeller inspection runs before the bearings are replaced."""
    a0100, a0110, a0120 = _act(c, "A0100"), _act(c, "A0110"), _act(c, "A0120")
    jobs = plan_rules._jobs(c)

    def broken(text: str) -> dict[int, str]:
        return {h["item_id"]: h["say"] for h in plan_rules.check(plan_rules.read(c, text), jobs)}

    longer = broken("no MECH job longer than 40 h")
    assert a0110["id"] in longer and a0100["id"] not in longer and longer[a0110["id"]] == "runs 48 h, over 40 h"
    assert a0120["id"] not in longer, "an NDT job isn't on the fitters"
    assert plan_rules.read(c, "no MECH job longer than 40 h")["say"] == "no job on Mechanical fitters longer than 40 h"
    together = broken("no overlap on MECH")
    assert together[a0100["id"]] == f"at the same time as “{_act(c, 'A0300')['title']}”"
    assert not broken("no overlap on NDT"), "the inspectors' jobs follow one another"
    sequence = broken("A0110 before A0120")
    assert list(sequence) == [a0120["id"]] and "before A0110 is done" in sequence[a0120["id"]]
    assert not broken("A0100 before A0110"), "the logic already has A0110 after A0100"
    gap = plan_rules.read(c, "at least 400 h between jobs on SCAF")
    assert gap["form"] == "gap" and gap["say"] == "at least 400 h between jobs on Scaffolders"
    assert {h["say"].split(" h after ")[1] for h in plan_rules.check(gap, jobs)} <= {
        f"“{_act(c, code)['title']}” ends" for code in ("A0050", "A0060", "A0800")}
    assert plan_rules.read(c, "no overlap on crane")["resources"] == ["CRANE"]


def test_a_phrase_it_cannot_read_names_the_forms_and_a_name_not_on_the_plan_is_refused(c):
    with pytest.raises(PlanError, match="Fuzer reads five forms: A0130 done by 15 Apr; no job longer than 36 h"):
        plan_rules.read(c, "keep the fitters happy")
    with pytest.raises(PlanError, match="nothing on the plan is called A9999"):
        plan_rules.read(c, "A9999 done by 15 Apr")
    with pytest.raises(PlanError, match="no shared resource is called welders"):
        plan_rules.read(c, "no overlap on welders")
    with pytest.raises(PlanError, match="named person"):
        plan_rules.add(c, "no overlap on crane", "agent:planner")
    c.rollback()
