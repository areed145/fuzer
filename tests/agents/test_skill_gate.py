"""P6.4d: the eval gate. Publishing asks the pack's eval and the draft's questions of the live version and the draft;
a draft that scores lower cannot be published, and the refusal shows both scores."""

import pytest

from fuzer.agents import skill_gate, skill_versions

SKILL = "maintenance-integrity/compression"
STRAINER = "\n\nBefore a surge diagnosis, check the suction strainer differential pressure.\n"
ASKED = {"question": "Why is K-1 surging?", "must_mention": ["strainer"], "specialists": ["maintenance-integrity"]}


@pytest.fixture(scope="module")
def conn(okan_db):
    return okan_db.conn


@pytest.fixture(autouse=True)
def fresh(conn):
    conn.execute("delete from skill_version")
    conn.commit()


def _answer(seen):
    """A stand-in for the assistant: it mentions the strainer only when the playbook it loaded says to check it."""
    def answer(question, skill, playbook):
        seen.append((question, "strainer" in playbook))
        said = "Check the suction strainer." if "strainer" in playbook else "Check the anti-surge valve."
        return {"answer": said, "specialists": ["maintenance-integrity"]}
    return answer


def test_a_draft_that_scores_no_lower_is_published_with_both_scores(conn):
    v = skill_versions.draft(conn, SKILL, skill_versions.shipped(SKILL) + STRAINER, "agent:maintenance-integrity",
                             questions=[ASKED])["version"]
    qs = skill_gate.questions(conn, SKILL, v)
    assert qs[-1] == ASKED and len(qs) > 1  # the pack's own answer keys for this bundle, then the draft's
    seen = []
    with pytest.raises(skill_versions.VersionRefused):  # an agent never publishes, and nothing is spent finding out
        skill_gate.publish(conn, SKILL, v, "agent:maintenance-integrity", _answer(seen))
    assert seen == []
    out = skill_gate.publish(conn, SKILL, v, "olga", _answer(seen))
    ev = out["evaluation"]
    assert out["version"] == v and ev["allowed"] and ev["candidate"]["score"] > ev["live"]["score"]
    assert len(seen) == 2 * len(qs) and ("Why is K-1 surging?", True) in seen and ("Why is K-1 surging?", False) in seen
    assert skill_versions.history(conn, SKILL)[0]["eval"]["questions"] == len(qs)


def test_a_draft_that_scores_lower_cannot_be_published_and_the_refusal_shows_both_scores(conn):
    v1 = skill_versions.draft(conn, SKILL, skill_versions.shipped(SKILL) + STRAINER, "olga", questions=[ASKED])
    skill_gate.publish(conn, SKILL, v1["version"], "olga", _answer([]))
    v2 = skill_versions.draft(conn, SKILL, skill_versions.shipped(SKILL), "agent:maintenance-integrity")["version"]
    with pytest.raises(skill_gate.GateRefused) as e:
        skill_gate.publish(conn, SKILL, v2, "olga", _answer([]))
    r = e.value.result
    assert not r["allowed"] and r["candidate"]["score"] < r["live"]["score"] and r["live_version"] == v1["version"]
    assert "cannot be published" in str(e.value)
    assert skill_versions.live(conn, SKILL)["version"] == v1["version"]  # still live
    assert skill_versions.history(conn, SKILL)[0]["status"] == "draft"


def test_the_api_publishes_behind_the_gate(conn, monkeypatch):
    from fastapi.testclient import TestClient

    from fuzer.api import app

    monkeypatch.setattr(skill_gate, "_ask", _answer([]))
    client = TestClient(app)
    good = skill_versions.draft(conn, SKILL, skill_versions.shipped(SKILL) + STRAINER, "olga", questions=[ASKED])
    conn.commit()
    r = client.post("/work/skills/publish", json={"skill": SKILL, "by": "olga", "version": good["version"]})
    assert r.status_code == 200 and r.json()["evaluation"]["allowed"], r.text
    bad = skill_versions.draft(conn, SKILL, skill_versions.shipped(SKILL), "olga")
    conn.commit()
    r = client.post("/work/skills/publish", json={"skill": SKILL, "by": "olga", "version": bad["version"]})
    assert r.status_code == 409 and r.json()["detail"]["candidate"]["score"] < r.json()["detail"]["live"]["score"]
