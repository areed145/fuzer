"""P6.4b: skill versions as data. A published playbook is what the agents load, and rolling back is one action."""

import os
import tempfile

import pytest

from fuzer.agents import ledger, skill_versions
from fuzer.agents.capabilities import tools_by_agent
from fuzer.core import db

SKILL = "maintenance-integrity/compression"
STRAINER = "\n\nBefore a surge diagnosis, check the suction strainer differential pressure.\n"


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        yield c


@pytest.fixture(autouse=True)
def fresh(conn):
    conn.execute("delete from skill_version")
    conn.commit()


def test_a_published_version_is_what_the_agents_load_and_rolling_back_is_one_action(conn):
    shipped = skill_versions.shipped(SKILL)
    assert skill_versions.live(conn, SKILL)["version"] == 0 and skill_versions.roots(conn) == {}
    before = ledger.skill_versions(["maintenance-integrity"])[SKILL]

    v1 = skill_versions.draft(conn, SKILL, shipped + STRAINER, "olga", cases=[{"kind": "brief", "case": 7}],
                              questions=[{"question": "Is K-1 surging?", "must_mention": ["strainer"]}],
                              note="briefs missed a blocked strainer")
    assert v1["version"] == 1 and v1["status"] == "draft"
    assert skill_versions.live(conn, SKILL)["version"] == 0  # a draft is not live
    with pytest.raises(skill_versions.VersionRefused):
        skill_versions.publish(conn, SKILL, 1, "agent:maintenance-integrity")
    assert skill_versions.publish(conn, SKILL, 1, "olga")["version"] == 1
    conn.commit()  # the ledger reads the live versions on its own connection

    root = skill_versions.roots(conn)[SKILL]
    assert (root / "SKILL.md").read_text().endswith(STRAINER) and (root / "pack.yaml").is_file()
    assert ledger.skill_versions(["maintenance-integrity"])[SKILL] != before  # the ledger names the live version

    skill_versions.draft(conn, SKILL, shipped + STRAINER + "Then the anti-surge valve.\n", "olga")
    skill_versions.publish(conn, SKILL, 2, "olga")
    h = {v["version"]: v for v in skill_versions.history(conn, SKILL)}
    assert [h[n]["status"] for n in (2, 1, 0)] == ["published", "retired", "retired"]
    assert h[1]["cases"] == [{"kind": "brief", "case": 7}] and h[1]["questions"][0]["must_mention"] == ["strainer"]

    back = skill_versions.rollback(conn, SKILL, "olga")
    assert back["rolled_back"] == 2 and back["version"] == 1
    back = skill_versions.rollback(conn, SKILL, "olga")
    assert back["version"] == 0 and back["playbook"] == shipped and skill_versions.roots(conn) == {}
    conn.commit()
    assert ledger.skill_versions(["maintenance-integrity"])[SKILL] == before
    with pytest.raises(ValueError, match="nothing to roll back"):
        skill_versions.rollback(conn, SKILL, "olga")
    with pytest.raises(ValueError, match="rolled_back, not a draft"):
        skill_versions.publish(conn, SKILL, 2, "olga")


def test_a_playbook_change_cannot_give_the_agent_a_tool(conn):
    mine = set(tools_by_agent()["maintenance-integrity"])
    other = next(t for ts in tools_by_agent().values() for t in ts if t not in mine)
    with pytest.raises(ValueError, match=other):
        skill_versions.draft(conn, SKILL, f"Call `{other}` first.", "olga")
    with pytest.raises(ValueError, match="no skill"):
        skill_versions.draft(conn, "nowhere/nothing", "x", "olga")


def test_the_api_drafts_publishes_and_rolls_back(conn):
    from fastapi.testclient import TestClient

    from fuzer.api import app

    client = TestClient(app)
    r = client.post("/work/skills/versions", json={"skill": SKILL, "by": "olga",
                                                   "playbook": skill_versions.shipped(SKILL) + STRAINER})
    assert r.status_code == 200, r.text
    n = r.json()["version"]
    assert client.post("/work/skills/publish", json={"skill": SKILL, "by": "system", "version": n}).status_code == 403
    assert client.post("/work/skills/publish", json={"skill": SKILL, "by": "olga", "version": n}).json()["version"] == n
    assert client.get("/work/skills/versions", params={"skill": SKILL}).json()[0]["status"] == "published"
    assert client.post("/work/skills/rollback", json={"skill": SKILL, "by": "olga"}).json()["version"] == 0
    assert client.post("/work/skills/rollback", json={"skill": SKILL, "by": "olga"}).status_code == 409
