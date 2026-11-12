"""P6.5: the register learns from stewards. Values a source writes that name nothing are answered by people as aliases;
ten like answers make one proposed rule, with the values it would answer; accepting it answers the rest, and the next
rebuild resolves them, counted in the source's link coverage. Proved on Okan (a legacy permit system's prefix) and on
Kestrel (another's suffix), with nothing about either written in the code."""

import pytest
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.core.pack import current_pack
from fuzer.ops import pipeline
from fuzer.sources import aliases, learned

pytestmark = pytest.mark.sequence


def _unresolved(conn, source="ptw", rel="covers") -> dict:
    r = conn.execute("select stats->'coverage' as c from source_match_stat where source = %s order by id desc limit 1",
                     (source,)).fetchone()["c"]
    return {"by_rel": r["by_rel"][rel],
            "values": [x["value"] for x in (r.get("unresolved") or {}).get(rel, {}).get("values", [])]}


def _meant(conn, value: str) -> str:
    """The entity a legacy value means, as a steward looking it up would find it: the equipment its digits number."""
    digits = "".join(c for c in value if c.isdigit())
    return conn.execute("select fuzer_id from crosswalk where source = 'sap' and record_type = 'equipment' "
                        "and source_key = %s", (digits,)).fetchone()["fuzer_id"]


def _learn(conn, affix: tuple[str, str]) -> dict:
    before = _unresolved(conn)
    values = sorted(before["values"])
    assert len(values) > learned.LEARN_AFTER, before  # the legacy permits name their location in a way nothing reads
    # nine answers aren't enough; the tenth like one makes the proposal
    for v in values[: learned.LEARN_AFTER - 1]:
        aliases.add(conn, v, _meant(conn, v), "steward", source="ptw", reason="looked it up")
    assert not [p for p in learned.proposals(conn) if p["source"] == "ptw"]
    tenth = values[learned.LEARN_AFTER - 1]
    aliases.add(conn, tenth, _meant(conn, tenth), "steward", source="ptw")
    [p] = [p for p in learned.proposals(conn) if p["source"] == "ptw"]
    assert (p["kind"], p["prefix"], p["suffix"]) == ("sap:equipment", *affix) and p["taught"] == learned.LEARN_AFTER, p
    rest = values[learned.LEARN_AFTER:]
    assert sorted(v["value"] for v in p["values"]) == rest and p["would_answer"] == len(rest)
    assert all(v["fuzer_id"] == _meant(conn, v["value"]) for v in p["values"])  # each the entity a person would pick

    out = learned.accept(conn, "ptw", p["kind"], p["prefix"], p["suffix"], "steward", reason="the old permit system")
    assert out["recorded"] == len(rest) and not [q for q in learned.proposals(conn) if q["source"] == "ptw"]
    conn.commit()
    pipeline.rebuild(conn, current_pack(conn))
    after = _unresolved(conn)
    assert after["values"] == [] and after["by_rel"][0] == before["by_rel"][0] + len(values), (before, after)
    assert after["by_rel"][1] == before["by_rel"][1]  # the same rows, now all resolved
    covered = conn.execute("select count(distinct e.src) as n from edge e join entity p on p.fuzer_id = e.src "
                           "and p.kind = 'permit' where e.rel = 'covers'").fetchone()["n"]
    assert covered == after["by_rel"][0]
    return out["rule"]


def test_ten_like_answers_teach_a_rule_that_answers_the_rest_on_okan(okan_db):
    conn = okan_db.conn
    rule = _learn(conn, ("eq", ""))
    [listed] = learned.listing(conn)
    assert listed["status"] == "accepted" and listed["answers"] > 0 and "SAP equipment key with 'EQ' before" in listed["rule"]

    # withdrawn, its answers retire (the stewards' own stay) and the values are unresolved again on the next rebuild
    out = learned.withdraw(conn, rule["id"], "steward", reason="checking")
    assert out["retired"] == listed["answers"]
    assert conn.execute("select count(*) as n from entity_alias where retired_at is null and source = 'ptw'"
                        ).fetchone()["n"] == learned.LEARN_AFTER
    conn.commit()
    pipeline.rebuild(conn, current_pack(conn))
    assert len(_unresolved(conn)["values"]) == listed["answers"]
    # a withdrawn rule is the people's to re-accept, not proposed again on its own
    assert not [p for p in learned.proposals(conn) if p["source"] == "ptw"]

    # rejected, it isn't proposed again; a rule no answer supports can't be accepted
    learned.reject(conn, "ptw", "sap:equipment", "zz", "", "steward")
    with pytest.raises(ValueError):
        learned.accept(conn, "ptw", "sap:equipment", "zz", "", "steward")
    conn.commit()

    from fastapi.testclient import TestClient

    from fuzer import api

    with TestClient(api.app) as client:  # the page reads and decides the same rules
        got = client.get("/stewardship/learned").json()
        assert got["proposals"] == [] and {r["status"] for r in got["rules"]} == {"withdrawn", "rejected"}
        r = client.post("/stewardship/learned/accept", json={"source": "ptw", "kind": "sap:equipment", "prefix": "eq",
                                                            "by": "steward"})
        assert r.status_code == 200 and r.json()["recorded"] == listed["answers"], r.text
        assert client.post("/stewardship/learned/accept", json={"source": "ptw", "kind": "sap:equipment",
                                                               "prefix": "zz", "by": "steward"}).status_code == 400


def test_an_accepted_rule_answers_new_values_after_a_rebuild_on_kestrel(request):
    conn = request.getfixturevalue("kestrel")
    rule = _learn(conn, ("", "leg"))
    # a value it answered is retired by hand: unresolved on the next rebuild, the rule answers it on the one after
    a = conn.execute("select id, value from entity_alias where created_by = %s and retired_at is null limit 1",
                     (f"rule:{rule['id']}",)).fetchone()
    aliases.retire(conn, a["id"], "steward")
    conn.commit()
    stats = pipeline.rebuild(conn, current_pack(conn))
    assert _unresolved(conn)["values"] == [a["value"]] and stats["learned_rules"] == {"rules": 1, "recorded": 0}
    stats = pipeline.rebuild(conn, current_pack(conn))
    assert _unresolved(conn)["values"] == [] and stats["learned_rules"] == {"rules": 1, "recorded": 1}
