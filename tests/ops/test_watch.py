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


@pytest.mark.unit
def test_a_provider_reply_with_a_thinking_block_reads_as_its_text(monkeypatch):
    """The provider returns SDK blocks, not dicts; a thinking block carries no answer (found on the first live run)."""
    from types import SimpleNamespace as NS

    reply = NS(content=[NS(type="thinking", thinking="..."), NS(type="text", text='{"proposals": []}')])
    monkeypatch.setattr(llm, "client", lambda scope: None)
    monkeypatch.setattr(llm, "call", lambda client, params, scope: reply)
    monkeypatch.setattr(llm, "usage", lambda msg: {})
    monkeypatch.setattr(llm, "cost_report", lambda name, usage, scope: {"cost_usd": 0})
    monkeypatch.setattr(settings, "get", lambda conn, key: "a-model")
    assert watch.model_ask("why?")["answer"] == '{"proposals": []}'


def test_what_a_point_measures_is_its_suffix_or_its_tags_instrument_letters():
    from fuzer.analysis.clusters import _measure

    assert _measure("OK_NWP_G3.QGAS") == "QGAS" and _measure("OK_J12_02.choke") == "CHOKE"
    assert _measure("OKA_PP.10FT1001A.PV") == "FT" and _measure("OK_GGCP:20-TT-2350A.PV") == "TT"


@pytest.mark.integration
def test_the_watch_is_shown_a_choke_plugging_over_weeks_and_not_the_fields_decline(okan_db):
    # P3.3c: OK-NWP-G3's choke plugs with scale through August 2025: its gas rate falls a sixth at the same choke while
    # the other gas wells hold. The week's model learns that as normal; against its peers and three months back it
    # stands out. The oil wells' pressures declining together, and chokes moved in one step, are not drifts.
    from fuzer.analysis import clusters

    conn = okan_db.conn
    a, b = "2025-08-20T00:00", "2025-08-22T00:00"
    nwp = next(x for x in watch.areas(conn) if x["label"] == "OKAN-50")
    found = clusters.drifts(conn, nwp["id"], a, b)
    g3 = next(d for d in found if d["point"] == "OK_NWP_G3.QGAS")
    assert g3["change"] < -0.1 and abs(g3["peers_change"]) < 0.02 and g3["peers"] == 2
    assert not [d for d in found if d["point"].endswith(".WHP")]  # the wells' pressures fall together
    ev = watch.evidence(conn, nwp, watch._t(a), watch._t(b))
    assert "OK_NWP_G3.QGAS" in {d["name"] for d in ev["drifts"]} and g3["point_id"] in watch._ids(ev)

    seen = []

    def ask(prompt):
        seen.append(json.loads(prompt.split("Evidence:\n", 1)[1]))
        d = seen[-1]["drifts"][0] if seen[-1]["drifts"] else None
        props = [{"subject": d["name"], "start": d["since"], "why": "falling at the same choke", "evidence": [d["id"]]}] if d else []
        return {"answer": "```json\n" + json.dumps({"proposals": props}) + "\n```", "usage": {"cost_usd": 0.0}}

    props, _ = watch.propose(conn, nwp, watch._t(a), watch._t(b), ask)
    assert seen and any(p["subject"] == "OK_NWP_G3.QGAS" and p["cited"] for p in props)  # asked on the drift alone
