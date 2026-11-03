"""The opportunity scan (#102): gains left on the table, not problems, so the work board (#99) doesn't
rank only what went wrong. It runs daily after the daily report, from what already exists:

- gas lift reallocation: on each lift gas header, gas moved from wells past their optimum (one more
  kSm3/d gains less than the breakeven) to wells short of theirs, from each well's gas lift curve. The
  gain is the gas moved times the difference in their marginal response, and the header's total gas is
  unchanged;
- wells below expectation: the latest test under the well's decline (calcs.decline), and flowing wells
  whose latest test is stale;
- recurring constraints: units that keep failing and losing oil (bad actors), worth a permanent fix
  rather than another repair;
- recoverable deferment: wells shut in by downtime that hasn't ended, for someone to check what they wait on.

Back-pressure options (#107) come from `debottleneck_option` the same way.

Each opportunity is kept in `opportunity` under a stable key, with its gain, basis, confidence, cited
evidence and what would confirm it, and becomes an `opportunity` work item (#97) scored by #100. An engineer
accepts it, rejects it (with a reason) or turns it into a board project (`decide`). A rejected or accepted
opportunity isn't raised again unless the data changes materially: its gain moves by half or more, or its
evidence (the tests or records behind it) changes. Proposals only, never operating instructions.
"""

import hashlib
import json
from datetime import date

from psycopg.types.json import Jsonb

BREAKEVEN = 0.5  # Sm3/d oil per kSm3/d lift gas: past this, more gas isn't worth it
STALE_DAYS = 90
MATERIAL = 0.5  # a gain this much higher or lower is a different opportunity
DECIDED = ("rejected", "accepted", "project")


def _opp(key: str, kind: str, title: str, gain: float | None, basis: str, confidence: str, evidence: list[str],
         confirm_by: str, target: str | None = None, **detail) -> dict:
    return {"key": key, "kind": kind, "title": title, "target": target,
            "gain_sm3d": None if gain is None else round(gain, 1), "basis": basis, "confidence": confidence,
            "evidence": [e for e in dict.fromkeys(evidence) if e], "confirm_by": confirm_by, **detail}


def gas_lift_moves(conn, at: str | None = None, breakeven: float = BREAKEVEN) -> list[dict]:
    from . import calcs, operations

    net = operations.gas_lift_network(conn, limit=100000)
    if "error" in net:
        return []
    by_header: dict[str, list[dict]] = {}
    for w in net["most_response"]:
        try:
            g = calcs.gas_lift_fit(conn, w["id"], breakeven, at)
        except calcs.CalcError:
            continue
        if not g["g_now"] or g["c"] <= 0:
            continue
        by_header.setdefault(w["header"] or "no header", []).append(
            {"id": w["id"], "well": w["well"], "g_now": g["g_now"], "g_opt": g["g_opt"], "marginal": g["marginal"],
             "c": g["c"], "k": g["k"], "record": g["tests"][-1]["record"]})
    out = []
    for header, ws in by_header.items():
        donors = sorted((w for w in ws if w["g_now"] > w["g_opt"] + 0.5), key=lambda w: w["marginal"])
        takers = sorted((w for w in ws if w["g_now"] < w["g_opt"] - 0.5), key=lambda w: -w["marginal"])
        moves, gain = [], 0.0
        spare = {w["id"]: w["g_now"] - w["g_opt"] for w in donors}
        room = {w["id"]: w["g_opt"] - w["g_now"] for w in takers}
        for d in donors:
            for t in takers:
                if t["marginal"] <= d["marginal"] or spare[d["id"]] <= 0.05 or room[t["id"]] <= 0.05:
                    continue
                q = round(min(spare[d["id"]], room[t["id"]]), 1)
                if q <= 0:
                    continue
                spare[d["id"]] -= q
                room[t["id"]] -= q
                g = q * (t["marginal"] - d["marginal"])
                gain += g
                moves.append({"from": d["well"], "from_id": d["id"], "to": t["well"], "to_id": t["id"], "ksm3d": q,
                              "from_marginal": round(d["marginal"], 3), "to_marginal": round(t["marginal"], 3),
                              "gain_sm3d": round(g, 1)})
        if moves and gain >= 1.0:
            out.append(_opp(f"gas_lift:{header}", "gas_lift_reallocation",
                            f"Reallocate lift gas on {header}: {len(moves)} move{'s' if len(moves) > 1 else ''}", gain,
                            f"each well's gas lift curve from its tests (decline taken out); gas moves from wells past the "
                            f"{breakeven} Sm3 oil per kSm3 optimum to wells short of it, the header's total unchanged; gain = "
                            "gas moved x difference in marginal response", "medium",
                            [w["record"] for w in ws if any(w["id"] in (m["from_id"], m["to_id"]) for m in moves)],
                            "a step-rate test on the receiving wells, then well tests after the move", None,
                            header=header, moves=moves, lift_gas_moved_ksm3d=round(sum(m["ksm3d"] for m in moves), 1)))
    return out


def wells_below_expectation(conn, at: str | None = None) -> list[dict]:
    from . import calcs, operations

    today = date.fromisoformat(at[:10] if at else operations.as_of(conn))
    out = []
    for r in conn.execute("select fuzer_id from entity where kind = 'well' order by 1"):
        try:
            d = calcs.decline(conn, r["fuzer_id"], until=at)
        except calcs.CalcError:
            continue
        label = calcs._label(conn, r["fuzer_id"])
        if d["flag"] == "below_decline":
            out.append(_opp(f"below_decline:{r['fuzer_id']}", "below_expectation",
                            f"{label} tested {d['value']} Sm3/d, under its decline's {d['expected']}", d["expected"] - d["value"],
                            d["basis"], "medium", d["sources"], "a retest; if it holds, a slickline or gas lift valve check",
                            r["fuzer_id"], latest_test=d["at"]))
        if (today - date.fromisoformat(d["at"])).days > STALE_DAYS:
            out.append(_opp(f"stale_test:{r['fuzer_id']}", "stale_test", f"{label}: last tested {d['at']}", None,
                            f"no well test in {STALE_DAYS} days: its rate and allocation rest on an old test", "low",
                            d["sources"], "a well test", r["fuzer_id"], latest_test=d["at"]))
    return out


def recurring_constraints(conn, min_failures: int = 4) -> list[dict]:
    from . import operations

    ba = operations.bad_actors(conn, limit=50)
    days = max((date.fromisoformat(ba["until"]) - date.fromisoformat(ba["since"])).days, 1)
    return [_opp(f"permanent_fix:{a['id']}", "recurring_constraint",
                 f"Permanent fix on {a['tag']}: {a['failures']} failures, {a['lost_oil_sm3']:,.0f} Sm3 lost",
                 a["lost_oil_sm3"] / days,
                 f"SAP corrective notifications and EC downtime {ba['since']} to {ba['until']}; gain = oil lost per day",
                 "low", [a["id"]], "a root cause analysis of the repeat failure modes", a["id"],
                 failures=a["failures"], repeat_failure_modes=a.get("repeat_failure_modes"))
            for a in ba["bad_actors"] if a["failures"] >= min_failures and (a["lost_oil_sm3"] or 0) > 0]


def recoverable_deferment(conn, at: str | None = None) -> list[dict]:
    from . import calcs, operations

    today = at[:10] if at else operations.as_of(conn)
    out = []
    for r in conn.execute(
        """
        select d.fuzer_id as id, max(g.value #>> '{}') filter (where g.field = 'start') as start,
               max(g.value #>> '{}') filter (where g.field = 'end') as "end",
               max(g.value #>> '{}') filter (where g.field = 'reason') as reason, a.dst as well
        from entity d join golden g using (fuzer_id) join edge a on a.src = d.fuzer_id and a.rel = 'affects'
        join entity w on w.fuzer_id = a.dst and w.kind = 'well'
        where d.kind = 'downtime' group by d.fuzer_id, a.dst
        """):
        if r["start"] and r["start"][:10] <= today and (not r["end"] or r["end"][:10] > today):
            try:
                t = calcs.well_tests(conn, r["well"], today)[-1]
            except calcs.CalcError:
                continue
            out.append(_opp(f"restart:{r['well']}", "recoverable_deferment",
                            f"{calcs._label(conn, r['well'])} shut in since {r['start'][:10]} ({r['reason'] or 'no reason'})",
                            t.get("oil_rate"), "the well's latest test rate, while its downtime record is open", "low",
                            [r["id"], t["record"]], "check what it waits on: if that's now available, plan the restart",
                            r["well"]))
    return out


def candidates(conn, at: str | None = None) -> list[dict]:
    return gas_lift_moves(conn, at) + wells_below_expectation(conn, at) + recurring_constraints(conn) + \
        recoverable_deferment(conn, at)


def _fingerprint(o: dict) -> str:
    return hashlib.sha1(json.dumps(sorted(o["evidence"])).encode()).hexdigest()[:16]


def _material(old: dict, new: dict) -> bool:
    """A decided opportunity comes back only if its evidence or its gain changed materially."""
    if old["fingerprint"] != _fingerprint(new):
        return True
    a, b = old["gain_sm3d"], new["gain_sm3d"]
    return a is not None and b is not None and abs(b - a) > MATERIAL * max(abs(a), 1e-9)


def scan(conn, pack, at: str | None = None) -> dict:
    """Find the day's opportunities, keep them, open a work item for each new one, close the ones the data no
    longer supports, and leave decided ones alone unless the data changed materially."""
    from . import value
    from .workflow import engine

    cfg = value.config(pack)
    found = {o["key"]: o for o in candidates(conn, at)}
    old = {r["key"]: dict(r) for r in conn.execute("select * from opportunity")}
    stats = {"found": len(found), "new": 0, "updated": 0, "still_decided": 0, "reopened": 0, "gone": 0}
    for key, o in found.items():
        prev = old.get(key)
        if prev and prev["status"] in DECIDED and not _material(prev, o):
            stats["still_decided"] += 1
            continue
        reopened = bool(prev and prev["status"] in DECIDED)
        item = value.Item("opportunity", f"scan:{key}", o["title"], o["basis"], None, o["target"], oil_sm3d=o["gain_sm3d"],
                          risk_applies=False, time_to_consequence_days=0.0,
                          sources={"oil_sm3d": o["basis"], "time_to_consequence_days": "gain starts when it is done"})
        usd = value.score(item, cfg)["value_usd"] if o["gain_sm3d"] is not None else None
        item_id = prev["item_id"] if prev and prev["status"] == "open" else None
        if item_id is None:
            item_id = engine.create_item(conn, "opportunity", o["title"], "agent:opportunity_scan",
                                         subjects=[s for s in [o["target"]] if s], priority=usd, role="engineer",
                                         payload={"opportunity": key, **{k: o[k] for k in ("gain_sm3d", "basis", "confidence",
                                                                                            "evidence", "confirm_by")}})["id"]
            stats["reopened" if reopened else "new"] += 1
        else:
            conn.execute("update work_item set priority = %s, title = %s, updated_at = now() where id = %s", (usd, o["title"], item_id))
            stats["updated"] += 1
        conn.execute(
            """
            insert into opportunity (key, kind, title, target, gain_sm3d, value_usd, detail, fingerprint, item_id, status, scanned_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', now())
            on conflict (key) do update set kind = excluded.kind, title = excluded.title, target = excluded.target,
              gain_sm3d = excluded.gain_sm3d, value_usd = excluded.value_usd, detail = excluded.detail,
              fingerprint = excluded.fingerprint, item_id = excluded.item_id, status = 'open', scanned_at = now()
            """, (key, o["kind"], o["title"], o["target"], o["gain_sm3d"], usd, Jsonb(o), _fingerprint(o), item_id))
    for key, prev in old.items():
        if key not in found and prev["status"] == "open":
            conn.execute("update opportunity set status = 'gone', scanned_at = now() where key = %s", (key,))
            if prev["item_id"]:
                try:
                    engine.transition(conn, prev["item_id"], "cancelled", "agent:opportunity_scan",
                                      "no longer supported by the data")
                except engine.WorkError:
                    pass
            stats["gone"] += 1
    conn.commit()
    return stats


def decide(conn, key: str, decision: str, by: str, reason: str | None = None) -> dict:
    """An engineer accepts, rejects (with a reason) or turns an opportunity into a board project."""
    from . import board
    from .workflow import engine

    if decision not in DECIDED:
        raise ValueError(f"decision must be one of {', '.join(DECIDED)}")
    if decision == "rejected" and not reason:
        raise ValueError("a rejection needs a reason")
    o = conn.execute("select * from opportunity where key = %s", (key,)).fetchone()
    if o is None:
        raise ValueError(f"no opportunity {key}")
    conn.execute("update opportunity set status = %s, decided_by = %s, decided_at = now(), reason = %s where key = %s",
                 (decision, by, reason, key))
    if o["item_id"]:
        engine.transition(conn, o["item_id"], "cancelled" if decision == "rejected" else "done", by, reason or decision)
    project = None
    if decision == "project":
        d = o["detail"]
        project = board.add_project(conn, o["title"], by, unit_id=o["target"] if (o["target"] or "").startswith("fz-tag") else None,
                                    benefit_usd=o["value_usd"], scope=f"{d.get('basis')}. Confirm by {d.get('confirm_by')}.")
    conn.commit()
    return {"key": key, "decision": decision, **({"project": project["id"]} if project else {})}


def listing(conn, status: str | None = "open", limit: int = 50) -> list[dict]:
    from .core.jsonsafe import plain as _plain

    return [_plain(dict(r)) for r in conn.execute(
        "select key, kind, title, target, gain_sm3d, value_usd, status, item_id, reason, detail from opportunity "
        "where %(s)s::text is null or status = %(s)s order by value_usd desc nulls last limit %(n)s",
        {"s": status, "n": limit})]
