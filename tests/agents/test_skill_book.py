"""The skills page (Ops IQ, Skills): every skill with its live version, a draft waiting and where it fell short; one
skill with its playbook and eval questions; a version against the live playbook. Reading calls no model."""

import pytest

from fuzer.agents import skill_book, skill_versions
from fuzer.ops import skill_review

SKILL = "maintenance-integrity/compression"


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


@pytest.fixture(autouse=True)
def fresh(conn):
    conn.execute("delete from skill_version")
    conn.commit()


def test_every_skill_is_listed_by_area_with_its_live_version_and_a_draft_waiting(conn):
    v = skill_versions.draft(conn, SKILL, skill_versions.shipped(SKILL) + "\nCheck the strainer.\n", "olga",
                             note="strainer first")["version"]
    out = skill_book.listing(conn)
    rows = {r["skill"]: r for r in out["skills"]}
    assert len(rows) > 20 and "ai" in out and set(out["ai"]) == {"available", "why"}
    row = rows[SKILL]
    assert row["title"] == "Compression" and row["area_label"] == "Maintenance & Integrity"
    assert row["live"]["version"] == 0 and row["draft"]["version"] == v and row["draft"]["note"] == "strainer first"
    assert row["questions"] > 0  # the pack's own answer keys for this bundle
    assert rows["shared/charts"]["area_label"] == "Shared by every agent"


def test_one_skill_reads_its_playbook_tools_and_questions_and_a_draft_reads_against_the_live_one(conn):
    d = skill_book.detail(conn, SKILL)
    assert d["playbook"].startswith("---") and not d["body"].startswith("---")
    assert "rotating_screen" in d["named_tools"] and set(d["named_tools"]) <= set(d["tools"])
    assert d["questions"] and d["agents"] == ["Maintenance & Integrity"]
    v = skill_versions.draft(conn, SKILL, d["playbook"].replace("rotating_screen(at)", "rotating_screen(at) first"),
                             "olga")["version"]
    diff = skill_book.diff(conn, SKILL, v)
    assert diff["added"] == 1 and diff["removed"] == 1 and diff["against"] == 0 and diff["questions"] == len(d["questions"])
    assert [x["op"] for x in diff["lines"]].count("same") > 10


def test_a_drafted_change_opens_as_work_that_links_to_its_draft(conn):
    v = skill_versions.draft(conn, SKILL, skill_versions.shipped(SKILL) + "\nCheck the strainer.\n",
                             "agent:maintenance-integrity")["version"]
    item = skill_review.open_review(conn, SKILL, v, "Compression: check the strainer", "agent:maintenance-integrity")
    conn.commit()
    row = next(r for r in skill_book.listing(conn)["skills"] if r["skill"] == SKILL)
    assert row["draft"]["review_case"] == item["item_id"]
    evidence = conn.execute("select evidence::text as e from exception_record where type = 'skill_change' and key = %s",
                            (skill_review.key_of(SKILL, v),)).fetchone()["e"]
    assert f"#/skills?skill=maintenance-integrity%2Fcompression&tab=versions&v={v}" in evidence
    skill_review.close_review(conn, SKILL, v, "olga", "test")
    conn.commit()


def test_the_api_reads_the_skills(conn):
    from fastapi.testclient import TestClient

    from fuzer.api import app

    client = TestClient(app)
    assert client.get("/work/skills").status_code == 200
    assert client.get("/work/skills/detail", params={"skill": SKILL}).json()["title"] == "Compression"
    assert client.get("/work/skills/detail", params={"skill": "no/such"}).status_code == 409
    assert client.get("/work/skills/diff", params={"skill": SKILL, "version": 7}).status_code == 409
