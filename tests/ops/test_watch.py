"""P3.3a (#931): the agentic watch reads a process area at a time and proposes what no detector raised, with its
evidence, why it is unusual and what it ruled out. In shadow its proposals are kept, never routed. With no model
provider it is off and says so. On Okan, the days before train B's March 2024 trip, as 20-K-2200B's vibration climbs."""

import json
import re

import pytest

from fuzer.core import detectors, llm, settings
from fuzer.core.pack import load_pack
from fuzer.ops import watch

PACK = load_pack("okan")
SINCE, UNTIL = "2024-03-08T00:00", "2024-03-10T00:00"


def scripted(cost=0.01):
    """A stand-in model: proposes the top point of each unexplained stretch it is shown, one proposal citing an id it
    wasn't shown, and one on a point a detection already covers."""
    def ask(prompt: str) -> dict:
        ev = json.loads(prompt.split("Evidence:\n", 1)[1])
        props = [{"subject": s["points"][0]["name"], "start": s["start"], "end": s["end"],
                  "why": "its area's model cannot explain it", "ruled_out": "no planned change in the log",
                  "evidence": [s["points"][0]["id"]]} for s in ev["unexplained_stretches"][:2]]
        props.append({"subject": "made up", "start": ev["since"], "why": "a hunch", "evidence": ["fz-tag-nothere"]})
        props += [{"subject": d["point"], "start": d["start"], "end": d["end"], "why": "already found",
                   "evidence": [d["id"]]} for d in ev["detections"][:1]]
        return {"answer": "Here is what I see.\n\n```json\n" + json.dumps({"proposals": props}) + "\n```",
                "usage": {"cost_usd": cost}}
    return ask


@pytest.mark.integration
def test_the_watch_in_shadow_keeps_proposals_and_routes_nothing(okan_db, monkeypatch):
    conn = okan_db.conn
    monkeypatch.setattr(llm, "configured", lambda scope="llm": False)
    off = watch.watch(conn, PACK, SINCE, UNTIL)
    assert off["status"] == "off" and "No model provider" in off["note"] and not off["proposals"]
    run = detectors.run(conn, PACK, "watch", since=SINCE, until=UNTIL)
    assert run["status"] == detectors.NOT_APPLICABLE and "a model provider" in run["note"]

    settings.set(conn, "watch.max_areas", 3, by="test")
    areas = watch.areas(conn, 3)
    assert len(areas) == 3
    ev = watch.evidence(conn, areas[0], watch._t(SINCE), watch._t(UNTIL))
    assert set(ev) >= {"unexplained_stretches", "detections", "open_exceptions", "alarms", "log", "notifications"}

    # a detection already made on the first stretch's top point: a proposal on it is dropped
    stretch = next((s for a in areas for s in watch.evidence(conn, a, watch._t(SINCE), watch._t(UNTIL))
                    ["unexplained_stretches"]), None)
    assert stretch, "the days before the trip have a stretch some area's model can't explain"
    top = stretch["points"][0]
    conn.execute("insert into detection (point_id, detector, key, start, \"end\") values (%s, 'steps', 'step', %s, %s)",
                 (top["id"], stretch["start"], stretch["end"]))
    before = {t: conn.execute(f"select count(*) as n from {t}").fetchone()["n"] for t in ("exception_record", "work_item")}
    out = watch.watch(conn, PACK, SINCE, UNTIL, ask=scripted())
    assert out["status"] == "shadow" and out["proposals"] and not out["skipped"], out
    assert all(p["why"] and p["area"] for p in out["proposals"])
    made_up = [p for p in out["proposals"] if p["subject"] == "made up"]
    assert made_up and not any(p["cited"] for p in made_up)  # an id it wasn't shown: kept, marked uncited
    assert any(p["cited"] for p in out["proposals"])
    assert not any(p["why"] == "already found" or p["subject"] == top["name"] and p["start"] == watch._t(stretch["start"])
                   for p in out["proposals"])  # a detection covers it: dropped
    rows = conn.execute("select mode, why, evidence from watch_proposal where since = %s", (watch._t(SINCE),)).fetchall()
    assert len(rows) == len(out["proposals"]) and {r["mode"] for r in rows} == {"shadow"}
    after = {t: conn.execute(f"select count(*) as n from {t}").fetchone()["n"] for t in ("exception_record", "work_item")}
    assert after == before  # never routed: no exception, no case

    # the spend cap: the first area that asks spends it, the rest are not read and the run says so
    settings.set(conn, "watch.max_usd", 0.5, by="test")
    capped = watch.watch(conn, PACK, SINCE, UNTIL, ask=scripted(cost=1.0))
    assert capped["skipped"] and re.search(r"spend cap", capped["note"])
    conn.commit()
