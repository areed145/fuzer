"""Safeguards and their bypasses (P3.9a, #763), read from the safeguard register and the bypass register, never from
tag names:

    safeguard   a trip, interlock or shutdown: the instrument it acts on and the equipment it protects
    bypass      a safeguard taken out of service: its state, when it went in, when it must come out (expires), why,
                who authorised it and the change record it runs under

A bypass is in place while its state says so and it went in before the time asked about. One still in place past its
expiry is overdue, and the `bypass_overdue` detector raises it as work for the ops lead until it is removed or its
expiry moves out.

`bypasses(conn, subjects)` answers for what a job or a case is about: the safeguards that act on or protect a subject,
what is installed at one or where one is installed, and, with `system=True`, the rest of the train it is part of
(graph.structure, from piping). Simultaneous operations, the evidence banner and the refusal of an agent proposal
(P3.9b, P3.9c) read it from here.

A safeguard is proof tested every so many months (IEC 61511): `proof_tests(conn, subjects)` reads its interval and
last test from the register, and one past its interval, or with an interval and no test on file, is overdue. The
`proof_test_overdue` detector raises it as work for the engineer until a later test is on file (P4.2e, #850).

Each time a safeguard was called on to act is in the site's demand log (IEC 61511-1 asks for it): `demands(conn)` reads
the last year of it per safeguard against the demand rate the register says it was designed for. A demand it failed
to act on, or more real demands than that rate allows (and at least two: one demand in a year says little), is raised
by the `safeguard_demands` detector for the engineer, as the risk assessment behind it assumed otherwise. A spurious
trip is listed but is not a demand.
"""

from datetime import UTC, datetime, timedelta

from ..core import clock, detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact

IN_PLACE = ("active", "in place", "applied", "bypassed", "inhibited", "overridden", "forced")


def _when(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return clock.aware(t)


def loaded(conn) -> bool:
    return conn.execute("select 1 from entity where kind = 'bypass' and retired_at is null limit 1").fetchone() is not None


def _around(conn, ids: list[str]) -> set[str]:
    """`ids`, what is installed at or is part of one (its instruments), and where one is installed."""
    if not ids:
        return set()
    return {r["id"] for r in conn.execute(
        "select unnest(%(s)s::text[]) as id union select src from edge where rel in ('installed_at', 'part_of') "
        "and dst = any(%(s)s) and valid_to is null union select dst from edge where rel = 'installed_at' "
        "and src = any(%(s)s) and valid_to is null", {"s": list(ids)}).fetchall()}


def reach(conn, subjects: list[str], system: bool = False, plant=None) -> list[str]:
    """The subjects, what is installed at or part of them and where they are installed, and with `system` the rest of
    each one's train (and what drives it) and what is installed on or part of that. A caller asking for several
    passes the `plant` (structure.load) it read once: reading it is the slow part."""
    near = _around(conn, list(subjects or []))
    if system and near:
        from ..graph import structure

        plant = plant or structure.load(conn)
        for s in list(near):
            t = structure.train(conn, s, plant)
            if t.get("known"):
                near |= set(t["members"])
                near |= {d for d in (structure.driver(conn, m, plant) for m in t["members"]) if d}
        near = _around(conn, sorted(near))
    return sorted(near)


def _rows(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """
        select b.fuzer_id as id, bl.label, s.fuzer_id as safeguard_id, sl.label as safeguard,
               max(case when g.field = 'state' then g.value #>> '{}' end) as state,
               max(case when g.field = 'bypassed_from' then g.value #>> '{}' end) as bypassed_from,
               max(case when g.field = 'expires' then g.value #>> '{}' end) as expires,
               max(case when g.field = 'reason' then g.value #>> '{}' end) as reason,
               max(case when g.field = 'authorised_by' then g.value #>> '{}' end) as authorised_by,
               max(case when g.field = 'moc' then g.value #>> '{}' end) as moc,
               (select sg.value #>> '{}' from golden sg where sg.fuzer_id = s.fuzer_id and sg.field = 'function')
                 as function,
               (select sg.value #>> '{}' from golden sg where sg.fuzer_id = s.fuzer_id and sg.field = 'action')
                 as action,
               (select coalesce(array_agg(e.dst order by e.dst), '{}') from edge e
                where e.src = s.fuzer_id and e.rel = 'protects' and e.valid_to is null) as protects,
               (select coalesce(array_agg(e.dst order by e.dst), '{}') from edge e
                where e.src = s.fuzer_id and e.rel = 'acts_on' and e.valid_to is null) as acts_on
        from entity b
        join edge l on l.src = b.fuzer_id and l.rel = 'bypasses' and l.valid_to is null
        join entity s on s.fuzer_id = l.dst and s.kind = 'safeguard'
        left join entity_label bl on bl.fuzer_id = b.fuzer_id
        left join entity_label sl on sl.fuzer_id = s.fuzer_id
        left join golden g on g.fuzer_id = b.fuzer_id
        where b.kind = 'bypass' and b.retired_at is null
        group by b.fuzer_id, bl.label, s.fuzer_id, sl.label
        order by bl.label
        """).fetchall()]


def _names(conn, ids: list[str]) -> list[str]:
    if not ids:
        return []
    got = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)", (list(ids),))}
    return [got.get(i) or i for i in ids]


def bypasses(conn, subjects: list[str] | None = None, at_time: datetime | None = None, *, system: bool = False,
             in_place: bool = True, plant=None) -> list[dict]:
    """The bypasses in place at `at_time` (default now), each with its safeguard, what that protects and acts on,
    whether it is overdue and by how long; on `subjects` (see `reach`) when given. `in_place=False` returns every
    bypass on file."""
    now = at_time or datetime.now(UTC)
    near = set(reach(conn, subjects, system, plant)) if subjects is not None else None
    out = []
    for r in _rows(conn):
        start, end = _when(r["bypassed_from"]), _when(r["expires"])
        live = (r["state"] or "").lower() in IN_PLACE and (start is None or start <= now)
        if in_place and not live:
            continue
        guards = list(r["protects"]) + list(r["acts_on"])
        if near is not None and not near & set(guards):
            continue
        overdue = live and end is not None and end < now
        out.append({**r, "protects": list(r["protects"]), "acts_on": list(r["acts_on"]),
                    "protects_labels": _names(conn, list(r["protects"])),
                    "acts_on_labels": _names(conn, list(r["acts_on"])), "in_place": live, "overdue": overdue,
                    "overdue_hours": round((now - end).total_seconds() / 3600, 1) if overdue else None,
                    "say": say(r, conn, overdue)})
    return out


def say(r: dict, conn=None, overdue: bool = False) -> str:
    """One line: which safeguard is bypassed, on what, until when, and under which change."""
    what = r.get("function") or r.get("safeguard") or "a safeguard"
    on = ", ".join(r.get("protects_labels") or (_names(conn, list(r.get("protects") or [])) if conn else []))
    until = _when(r.get("expires"))
    line = f"{what} ({r.get('safeguard')}) bypassed" + (f" on {on}" if on else "")
    if until:
        line += f", {'overdue since' if overdue else 'until'} {until:%d %b %H:%M}"
    line += f" under {r['moc']}" if r.get("moc") else ", no change record"
    return line + f" ({r.get('label')})"


# ---- overdue bypasses as work -------------------------------------------------------------------------------------

def _overdue_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("bypass_overdue") or exception_type(
        "bypass_overdue", "Bypass overdue", role="role:ops_lead", audience=("ioc",), joins=False,
        description="A safeguard still bypassed past the expiry it was authorised to. Put it back in service, or "
                    "extend it under a change with its risk assessed. It clears when the bypass is removed or its "
                    "expiry moves out.")


OVERDUE_TYPE = _overdue_type().name


@detectors.register("bypass_overdue", title="A safeguard still bypassed past its expiry",
                    kind=detectors.RULE, cadence="30min", raises=(OVERDUE_TYPE,),
                    needs={"safeguard bypass records": lambda conn, pack: loaded(conn)},
                    eval="test:tests/evals/test_safeguards_kestrel.py::"
                         "test_an_overdue_bypass_is_work_for_the_ops_lead_until_it_is_removed",
                    found=lambda out: len(out["raised"]))
def watch(conn, by: str = "agent:safeguards", now: datetime | None = None) -> dict:
    """Each bypass in place past its expiry raised as `bypass_overdue` for the ops lead (keyed by the bypass);
    open ones whose bypass is no longer overdue are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    now = now or datetime.now(UTC)
    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (OVERDUE_TYPE,))]
    late = [b for b in bypasses(conn, at_time=now) if b["overdue"]]
    raised, cleared, errors = [], [], []
    for b in late:
        found = Finding(
            "safeguards.bypass_overdue", DETERMINISTIC, method="Bypass register read against each bypass's expiry",
            start=b["expires"], data={"bypass": b["label"], "safeguard": b["safeguard"],
                                      "overdue_hours": b["overdue_hours"]},
            items=(fact("Safeguard", f"{b['safeguard']}: {b['function']}"), fact("Action", b["action"]),
                   fact("Protects", ", ".join(b["protects_labels"])), fact("Acts on", ", ".join(b["acts_on_labels"])),
                   at("Bypassed from", b["bypassed_from"]), at("Expired", b["expires"]),
                   fact("Overdue", b["overdue_hours"], "h"), fact("Reason", b["reason"]),
                   fact("Authorised by", b["authorised_by"]), fact("Change record", b["moc"] or "none on file")))
        title = f"{b['function'] or b['safeguard']} on {', '.join(b['protects_labels']) or 'the plant'} " \
                f"still bypassed, {b['overdue_hours']:g} h past its expiry"
        try:
            x = raise_exception(conn, OVERDUE_TYPE, b["label"], title, by, source="safeguards",
                                refs=[b["id"], b["safeguard_id"], *b["protects"], *b["acts_on"]], priority=0.9,
                                finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{b['label']}: {e}")
            continue
        raised.append({"bypass": b["label"], "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
    for key in set(held) - {b["label"] for b in late}:
        clear_exception(conn, OVERDUE_TYPE, key, by, reason="the bypass is removed or no longer past its expiry")
        cleared.append(key)
    conn.commit()
    return {"overdue": len(late), "raised": raised, "cleared": cleared, "errors": errors}


# ---- proof tests (P4.2e, #850) ------------------------------------------------------------------------------------

MONTH_DAYS = 365.25 / 12


def _number(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def proof_tests(conn, subjects: list[str] | None = None, at_time: datetime | None = None, *,
                system: bool = False) -> list[dict]:
    """Each safeguard with a proof test interval on the register: its last test, when the next is due, and whether it
    is overdue and by how many days; on `subjects` (see `reach`) when given. One with no test on file is overdue from
    the day it was asked about, as nothing shows it was ever tested."""
    now = at_time or datetime.now(UTC)
    near = set(reach(conn, subjects, system)) if subjects is not None else None
    out = []
    for r in conn.execute(
            """
            select s.fuzer_id as id, l.label,
                   max(case when g.field = 'function' then g.value #>> '{}' end) as function,
                   max(case when g.field = 'action' then g.value #>> '{}' end) as action,
                   max(case when g.field = 'sil' then g.value #>> '{}' end) as sil,
                   max(case when g.field = 'proof_test_interval_months' then g.value #>> '{}' end) as interval,
                   max(case when g.field = 'last_proof_test' then g.value #>> '{}' end) as last_test,
                   (select coalesce(array_agg(e.dst order by e.dst), '{}') from edge e
                    where e.src = s.fuzer_id and e.rel = 'protects' and e.valid_to is null) as protects,
                   (select coalesce(array_agg(e.dst order by e.dst), '{}') from edge e
                    where e.src = s.fuzer_id and e.rel = 'acts_on' and e.valid_to is null) as acts_on
            from entity s
            left join entity_label l on l.fuzer_id = s.fuzer_id
            left join golden g on g.fuzer_id = s.fuzer_id
            where s.kind = 'safeguard' and s.retired_at is null
            group by s.fuzer_id, l.label
            order by l.label
            """).fetchall():
        months = _number(r["interval"])
        if not months:
            continue
        guards = list(r["protects"]) + list(r["acts_on"])
        if near is not None and not near & set(guards):
            continue
        last = _when(r["last_test"])
        due = last + timedelta(days=months * MONTH_DAYS) if last else None
        overdue = due is None or due < now
        out.append({**dict(r), "interval_months": months, "protects": list(r["protects"]),
                    "acts_on": list(r["acts_on"]), "protects_labels": _names(conn, list(r["protects"])),
                    "acts_on_labels": _names(conn, list(r["acts_on"])),
                    "due": due.isoformat() if due else None, "overdue": overdue,
                    "overdue_days": (now - due).days if due and overdue else None})
    return out


def _proof_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("proof_test_overdue") or exception_type(
        "proof_test_overdue", "Proof test overdue", role="role:engineer", audience=("ioc",), joins=False,
        description="A safeguard past its proof test interval, or with no proof test on file. Until it is tested "
                    "the protection it claims is not shown to work. Plan the test, or record the one done. It clears "
                    "when a test within the interval is on file.")


PROOF_TYPE = _proof_type().name


@detectors.register("proof_test_overdue", title="A safeguard past its proof test interval",
                    kind=detectors.RULE, cadence="daily", raises=(PROOF_TYPE,),
                    needs={"proof test intervals on the safeguard register": lambda conn, pack: conn.execute(
                        "select 1 from golden g join entity s on s.fuzer_id = g.fuzer_id and s.kind = 'safeguard' "
                        "where g.field = 'proof_test_interval_months' limit 1").fetchone() is not None},
                    eval="test:tests/evals/test_safeguards_kestrel.py::"
                         "test_a_safeguard_past_its_proof_test_interval_is_work_until_it_is_tested",
                    found=lambda out: len(out["raised"]))
def watch_proof_tests(conn, by: str = "agent:safeguards", now: datetime | None = None) -> dict:
    """Each safeguard overdue for its proof test raised as `proof_test_overdue` for the engineer (keyed by the
    safeguard); open ones now tested within their interval are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    now = now or datetime.now(UTC)
    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (PROOF_TYPE,))]
    late = [p for p in proof_tests(conn, at_time=now) if p["overdue"]]
    raised, cleared, errors = [], [], []
    for p in late:
        on = ", ".join(p["protects_labels"]) or "the plant"
        what = p["function"] or p["label"]
        found = Finding(
            "safeguards.proof_test_overdue", DETERMINISTIC,
            method="Safeguard register read against each safeguard's proof test interval",
            start=p["due"], data={"safeguard": p["label"], "overdue_days": p["overdue_days"]},
            items=(fact("Safeguard", f"{p['label']}: {what}"), fact("Action", p["action"]), fact("SIL", p["sil"]),
                   fact("Protects", on), fact("Acts on", ", ".join(p["acts_on_labels"])),
                   fact("Interval", p["interval_months"], "months"),
                   at("Last proof test", p["last_test"]) if p["last_test"] else fact("Last proof test", "none on file"),
                   at("Due", p["due"]) if p["due"] else fact("Due", "now: no test on file"),
                   fact("Overdue", p["overdue_days"], "days")))
        title = f"{what} on {on}: " + (f"proof test {p['overdue_days']} days overdue" if p["due"]
                                         else "no proof test on file")
        try:
            x = raise_exception(conn, PROOF_TYPE, p["label"], title, by, source="safeguards",
                                refs=[p["id"], *p["protects"], *p["acts_on"]], priority=0.7, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{p['label']}: {e}")
            continue
        raised.append({"safeguard": p["label"], "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
    for key in set(held) - {p["label"] for p in late}:
        clear_exception(conn, PROOF_TYPE, key, by, reason="a proof test within its interval is on file")
        cleared.append(key)
    conn.commit()
    return {"overdue": len(late), "raised": raised, "cleared": cleared, "errors": errors}


# ---- demands on a safeguard (P4.2e, #850) ------------------------------------------------------------------------

DEMAND_DAYS = 365
LOW_DEMAND = 1.0  # demands a year a safeguard is taken to see when the register gives no rate (IEC 61508 low demand)
REAL, FAILED, SPURIOUS = ("worked", "success", "successful", "acted"), ("failed", "failure", "did not act"), \
    ("spurious", "false")


def _outcome(v: str | None) -> str:
    v = (v or "").strip().lower()
    return "failed" if v in FAILED else "spurious" if v in SPURIOUS else "worked" if v in REAL else "unknown"


def demands(conn, subjects: list[str] | None = None, at_time: datetime | None = None, *, days: int = DEMAND_DAYS,
            system: bool = False) -> list[dict]:
    """Each safeguard with a demand in the last `days`: its demands (worked, failed, unknown) and spurious trips,
    newest first, the rate a year they come to, the rate it was designed for, and whether it is `over` that rate or
    `failed` on one; on `subjects` (see `reach`) when given."""
    now = at_time or datetime.now(UTC)
    since = now - timedelta(days=days)
    near = set(reach(conn, subjects, system)) if subjects is not None else None
    by: dict[str, dict] = {}
    for r in conn.execute(
            """
            select d.fuzer_id as id, dl.label, s.fuzer_id as safeguard_id, sl.label as safeguard,
                   max(case when g.field = 'at' then g.value #>> '{}' end) as at,
                   max(case when g.field = 'outcome' then g.value #>> '{}' end) as outcome,
                   max(case when g.field = 'cause' then g.value #>> '{}' end) as cause
            from entity d
            join edge l on l.src = d.fuzer_id and l.rel = 'demands' and l.valid_to is null
            join entity s on s.fuzer_id = l.dst and s.kind = 'safeguard' and s.retired_at is null
            left join entity_label dl on dl.fuzer_id = d.fuzer_id
            left join entity_label sl on sl.fuzer_id = s.fuzer_id
            left join golden g on g.fuzer_id = d.fuzer_id
            where d.kind = 'demand' and d.retired_at is null
            group by d.fuzer_id, dl.label, s.fuzer_id, sl.label
            """).fetchall():
        t = _when(r["at"])
        if t is None or not since <= t <= now:
            continue
        s = by.setdefault(r["safeguard_id"], {"id": r["safeguard_id"], "label": r["safeguard"], "demands": [],
                                              "spurious": []})
        e = {"demand": r["label"], "at": t.isoformat(), "outcome": _outcome(r["outcome"]), "cause": r["cause"]}
        s["spurious" if e["outcome"] == "spurious" else "demands"].append(e)
    if not by:
        return []
    facts = {r["fuzer_id"]: r for r in conn.execute(
        """
        select s.fuzer_id,
               max(case when g.field = 'function' then g.value #>> '{}' end) as function,
               max(case when g.field = 'action' then g.value #>> '{}' end) as action,
               max(case when g.field = 'sil' then g.value #>> '{}' end) as sil,
               max(case when g.field = 'demand_rate_per_year' then g.value #>> '{}' end) as designed,
               (select coalesce(array_agg(e.dst order by e.dst), '{}') from edge e
                where e.src = s.fuzer_id and e.rel = 'protects' and e.valid_to is null) as protects,
               (select coalesce(array_agg(e.dst order by e.dst), '{}') from edge e
                where e.src = s.fuzer_id and e.rel = 'acts_on' and e.valid_to is null) as acts_on
        from entity s left join golden g on g.fuzer_id = s.fuzer_id
        where s.fuzer_id = any(%s) group by s.fuzer_id
        """, (list(by),))}
    out = []
    for sid, s in sorted(by.items(), key=lambda kv: kv[1]["label"] or kv[0]):
        f = facts.get(sid) or {}
        protects, acts_on = list(f.get("protects") or []), list(f.get("acts_on") or [])
        if near is not None and not near & set(protects + acts_on):
            continue
        designed = _number(f.get("designed"))
        allowed = (designed if designed is not None else LOW_DEMAND) * days / 365.25
        n = len(s["demands"])
        s["demands"].sort(key=lambda e: e["at"], reverse=True)
        s["spurious"].sort(key=lambda e: e["at"], reverse=True)
        out.append({**s, "function": f.get("function"), "action": f.get("action"), "sil": f.get("sil"),
                    "protects": protects, "acts_on": acts_on, "protects_labels": _names(conn, protects),
                    "days": days, "rate_per_year": round(n * 365.25 / days, 2), "designed_per_year": designed,
                    "allowed": round(allowed, 2), "over": n >= 2 and n > allowed,
                    "failed": [e for e in s["demands"] if e["outcome"] == "failed"]})
    return out


def _demand_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("safeguard_demand") or exception_type(
        "safeguard_demand", "Safeguard demand to review", role="role:engineer", audience=("ioc",), joins=False,
        description="A safeguard that failed to act on a demand, or was called on more often than the rate its risk "
                    "assessment assumed. Find what keeps creating the demand, and check whether the protection it "
                    "claims still holds at this rate. It clears when the last year of demands is back within the "
                    "rate and none failed.")


DEMAND_TYPE = _demand_type().name


@detectors.register("safeguard_demands", title="A safeguard failed on demand or was demanded more than designed",
                    kind=detectors.RULE, cadence="daily", raises=(DEMAND_TYPE,),
                    needs={"safeguard demand records": lambda conn, pack: conn.execute(
                        "select 1 from entity where kind = 'demand' and retired_at is null limit 1").fetchone()
                        is not None},
                    eval="test:tests/evals/test_safeguards_kestrel.py::"
                         "test_a_safeguard_demanded_more_than_designed_is_work_for_the_engineer",
                    found=lambda out: len(out["raised"]))
def watch_demands(conn, by: str = "agent:safeguards", now: datetime | None = None) -> dict:
    """Each safeguard that failed on a demand or is over its designed demand rate raised as `safeguard_demand` for
    the engineer (keyed by the safeguard); open ones back within the rate with no failure are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (DEMAND_TYPE,))]
    late = [d for d in demands(conn, at_time=now) if d["over"] or d["failed"]]
    raised, cleared, errors = [], [], []
    for d in late:
        on = ", ".join(d["protects_labels"]) or "the plant"
        what = d["function"] or d["label"]
        n = len(d["demands"])
        designed = f"{d['designed_per_year']:g} a year" if d["designed_per_year"] is not None else \
            f"none on the register, so {LOW_DEMAND:g} a year (low demand)"
        found = Finding(
            "safeguards.demand", DETERMINISTIC, method="Demand log read against the demand rate on the register",
            start=d["demands"][-1]["at"] if d["demands"] else None,
            data={"safeguard": d["label"], "demands": n, "failed": len(d["failed"]), "spurious": len(d["spurious"]),
                  "rate_per_year": d["rate_per_year"], "designed_per_year": d["designed_per_year"]},
            items=(fact("Safeguard", f"{d['label']}: {what}"), fact("Action", d["action"]), fact("SIL", d["sil"]),
                   fact("Protects", on), fact("Demands", n), fact("Over the last", d["days"], "days"),
                   fact("Designed for", designed), fact("Failed to act", len(d["failed"]) or None),
                   fact("Spurious trips", len(d["spurious"]) or None),
                   *(at(f"Demand: {e['outcome']}" + (f", {e['cause']}" if e["cause"] else ""), e["at"])
                     for e in d["demands"][:6])))
        title = (f"{what} on {on} failed to act on a demand" if d["failed"] else
                 f"{what} on {on} demanded {n} times in {d['days']} days, designed for {d['allowed']:g}")
        try:
            x = raise_exception(conn, DEMAND_TYPE, d["label"], title, by, source="safeguards",
                                refs=[d["id"], *d["protects"], *d["acts_on"]],
                                priority=0.95 if d["failed"] else 0.7, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{d['label']}: {e}")
            continue
        raised.append({"safeguard": d["label"], "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
    for key in set(held) - {d["label"] for d in late}:
        clear_exception(conn, DEMAND_TYPE, key, by, reason="its demands are within the designed rate and none failed")
        cleared.append(key)
    conn.commit()
    return {"reviewed": len(late), "raised": raised, "cleared": cleared, "errors": errors}


# ---- changes, and what a case should know before anything else (P3.9b) --------------------------------------------

CLOSED_CHANGE = ("closed", "cancelled", "canceled", "rejected", "withdrawn")


def changes(conn, subjects: list[str] | None = None, *, system: bool = False, open_only: bool = True) -> list[dict]:
    """Change records (the moc source's `moc_changes`) about `subjects` (see `reach`), open ones only by default."""
    near = reach(conn, subjects, system) if subjects is not None else None
    if near is not None and not near:
        return []
    rows = conn.execute(
        """
        select c.fuzer_id as id, cl.label,
               max(case when g.field = 'title' then g.value #>> '{}' end) as title,
               max(case when g.field = 'change_type' then g.value #>> '{}' end) as change_type,
               max(case when g.field = 'state' then g.value #>> '{}' end) as state,
               max(case when g.field = 'opened' then g.value #>> '{}' end) as opened,
               max(case when g.field = 'closes' then g.value #>> '{}' end) as closes,
               max(case when g.field = 'change_class' then g.value #>> '{}' end) as change_class,
               max(case when g.field = 'pssr' then g.value #>> '{}' end) as pssr,
               max(case when g.field = 'owner' then g.value #>> '{}' end) as owner,
               (select coalesce(array_agg(e.dst order by e.dst), '{}') from edge e
                where e.src = c.fuzer_id and e.rel = 'concerns' and e.valid_to is null) as about,
               (select coalesce(array_agg(coalesce(wl.label, e.dst) order by e.dst), '{}') from edge e
                left join entity_label wl on wl.fuzer_id = e.dst
                where e.src = c.fuzer_id and e.rel = 'carried_out_by' and e.valid_to is null) as work_orders
        from entity c
        left join entity_label cl on cl.fuzer_id = c.fuzer_id
        left join golden g on g.fuzer_id = c.fuzer_id
        where c.kind = 'change' and c.retired_at is null
          and (%(near)s::text[] is null or exists (select 1 from edge e where e.src = c.fuzer_id
               and e.rel = 'concerns' and e.valid_to is null and e.dst = any(%(near)s)))
        group by c.fuzer_id, cl.label order by cl.label
        """, {"near": near}).fetchall()
    return [{**dict(r), "about": list(r["about"]), "about_labels": _names(conn, list(r["about"])),
             "work_orders": list(r["work_orders"])} for r in rows
            if not open_only or (r["state"] or "").lower() not in CLOSED_CHANGE]


# ---- a change's stage (#755): its pre-startup review, and a temporary change past its end ----------------------------

PAST_IMPLEMENTATION = ("implemented", "pre-startup review", "pssr", "commissioned")


def stage(c: dict, now: datetime | None = None) -> dict:
    """What a change record's stage says to someone working on what it is about: a temporary change still open past
    the date it must close by (`expired`, with the days), or one implemented with its pre-startup safety review not
    done (`pssr_pending`). Read only from fields the MOC system gives; a field it doesn't give says nothing."""
    now = clock.aware(now) if now else datetime.now(UTC)
    state = (c.get("state") or "").lower()
    closes = _when(c.get("closes"))
    temporary = (c.get("change_class") or "").lower() == "temporary"
    expired = temporary and state not in CLOSED_CHANGE and closes is not None and closes < now
    pending = state in PAST_IMPLEMENTATION and (c.get("pssr") or "").lower() in ("pending", "not done", "open", "no")
    return {"temporary": temporary, "expired": expired,
            "overdue_days": round((now - closes).total_seconds() / 86400, 1) if expired else None,
            "pssr_pending": pending}


def change_warning(c: dict, now: datetime | None = None) -> str:
    """One line for dispatch and the phone about an open change record on the job's equipment."""
    st = stage(c, now)
    on = ", ".join(c.get("about_labels") or []) or "this equipment"
    title = (c.get("title") or "").rstrip(".")
    if st["expired"]:
        line = f"Temporary MOC {c['label']} on {on} is past its end date ({str(c['closes'])[:10]}) and still in place: {title}"
    elif st["pssr_pending"]:
        line = f"MOC {c['label']} on {on} is implemented but its pre-startup safety review is not done: {title}"
    else:
        kind = "Temporary MOC" if st["temporary"] else "Open MOC"
        line = f"{kind} {c['label']} on {on} ({c.get('state') or 'open'}): {title}"
    if c.get("work_orders"):
        line += f". Carried out by work order {', '.join(c['work_orders'])}"
    return line


def _case_subjects(conn, item_id: int) -> list[str]:
    rows = conn.execute("select subjects from exception_record where item_id = %s", (item_id,)).fetchall()
    found = [s for r in rows for s in r["subjects"] or []]
    if not found:
        row = conn.execute("select subjects from work_item where id = %s", (item_id,)).fetchone()
        found = list(row["subjects"] or []) if row else []
    return list(dict.fromkeys(found))


def on_case(conn, item_id: int, now: datetime | None = None) -> dict:
    """The bypasses in place on the system a case is about, and the open changes on it: what the evidence leads with,
    so nobody reads a trend or acts on a trip without knowing a safeguard is out."""
    subjects = _case_subjects(conn, item_id)
    if not subjects:
        return {"bypasses": [], "changes": [], "basis": "This case is about no equipment."}
    return {"bypasses": bypasses(conn, subjects, now, system=True), "changes": changes(conn, subjects, system=True),
            "basis": "Bypasses in place now on safeguards that act on or protect what this case is about, what is "
                     "installed at it or where it is installed, or the rest of its train by the piping; and open "
                     "change records about the same."}



# ---- a limit changed with no change record (P3.9c) -----------------------------------------------------------------

LOOKBACK_DAYS = 180  # limits changed within this long before now are checked
BEFORE_DAYS, AFTER_DAYS = 90, 2  # a change record opened this long before the change, or this soon after, covers it
SAY_TYPE = {"hihi": "high-high", "hi": "high", "lo": "low", "lolo": "low-low"}


def unrecorded(conn, now: datetime | None = None) -> list[dict]:
    """Alarm and IOW limits whose last change (the source's valid-from) falls in the last LOOKBACK_DAYS with no
    change record about the point or what it stands for opened from BEFORE_DAYS before to AFTER_DAYS after it.
    Only the latest change per limit is visible in the export, so only that one is claimed."""
    from datetime import timedelta

    from .exceptions import anchor_map

    now = clock.aware(now).astimezone(clock.zone()).replace(tzinfo=None) if now else clock.now()
    rows = conn.execute(
        """
        select pl.record, pl.limit_type, pl.point_id, pl.source, pl.basis, pl.value, pl.unit, pl.valid_from,
               coalesce(tl.label, pt.label, pl.point_id) as point
        from point_limit pl
        left join entity_label pt on pt.fuzer_id = pl.point_id
        left join lateral (  -- the tag the historian names the point for, before an AF element's attribute on it
            select h.src from edge h where h.rel = 'has_timeseries' and h.dst = pl.point_id and h.valid_to is null
            order by coalesce(h.props ? 'attribute', false), h.confidence desc, h.src limit 1) h on true
        left join entity_label tl on tl.fuzer_id = h.src
        where pl.valid_from >= %s and pl.valid_from <= %s and pl.valid_to is null
        order by pl.valid_from, pl.record, pl.limit_type
        """, (now - timedelta(days=LOOKBACK_DAYS), now)).fetchall()
    if not rows:
        return []
    stands = anchor_map(conn, list({r["point_id"] for r in rows}))
    recs = [c for c in changes(conn, open_only=False) if (c["state"] or "").lower() not in ("cancelled", "canceled",
                                                                                            "rejected", "withdrawn")]
    out = []
    for r in rows:
        when = r["valid_from"]
        mine = stands.get(r["point_id"], set()) | {r["point_id"]}
        cover = [c["label"] for c in recs if set(c["about"]) & mine and (o := _when(c["opened"])) is not None
                 and when - timedelta(days=BEFORE_DAYS) <= o.astimezone(clock.zone()).replace(tzinfo=None) <= when + timedelta(days=AFTER_DAYS)]
        if not cover:
            out.append({**dict(r), "valid_from": when.isoformat(timespec="minutes"), "key": f"{r['record']}#{r['limit_type']}"})
    return out


def _change_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("unrecorded_change") or exception_type(
        "unrecorded_change", "Change with no record", role="role:engineer", audience=("ioc",), joins=False,
        description="An alarm or integrity operating window limit that changed with no change record on file "
                    "around it. A question: link the change record, or say why none was needed. It clears when a "
                    "change record about it turns up.")


CHANGE_TYPE = _change_type().name


@detectors.register("unrecorded_changes", title="An alarm or IOW limit changed with no change record",
                    kind=detectors.RULE, cadence="daily", raises=(CHANGE_TYPE,),
                    needs={"change records": lambda conn, pack: conn.execute(
                        "select 1 from entity where kind = 'change' and retired_at is null limit 1").fetchone()
                        is not None},
                    eval="test:tests/evals/test_safeguards_kestrel.py::"
                         "test_a_limit_changed_with_no_change_record_is_a_question_until_one_turns_up",
                    found=lambda out: len(out["raised"]))
def watch_changes(conn, by: str = "agent:safeguards", now: datetime | None = None) -> dict:
    """Each limit changed with no change record raised as `unrecorded_change` for the engineer (keyed by the limit);
    open ones that a change record now covers, or that left the lookback, are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (CHANGE_TYPE,))]
    found_rows = unrecorded(conn, now)
    raised, cleared, errors = [], [], []
    for r in found_rows:
        what = f"{SAY_TYPE.get(r['limit_type'], r['limit_type'])} {'alarm' if r['basis'] == 'alarm' else r['basis']}"
        found = Finding(
            "safeguards.unrecorded_change", DETERMINISTIC, method="Limit changes read against the change records",
            start=r["valid_from"], data={"record": r["record"], "limit_type": r["limit_type"]},
            items=(fact("Point", r["point"]), fact("Limit", f"{what}: {r['value']:g} {r['unit'] or ''}".strip()),
                   at("Changed", r["valid_from"]), fact("Source", r["source"]),
                   fact("Looked for", f"a change record about it opened from {BEFORE_DAYS} days before to "
                                      f"{AFTER_DAYS} days after"),
                   fact("Note", "Only the latest change to each limit is in the export.")))
        try:
            x = raise_exception(conn, CHANGE_TYPE, r["key"], f"{r['point']} {what} changed "
                                f"{_when(r['valid_from']):%d %b %Y} with no change record", by, source="safeguards",
                                refs=[r["point_id"]], priority=0.6, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{r['key']}: {e}")
            continue
        raised.append({"key": r["key"], "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
    for key in set(held) - {r["key"] for r in found_rows}:
        clear_exception(conn, CHANGE_TYPE, key, by, reason="a change record covers it now")
        cleared.append(key)
    conn.commit()
    return {"unrecorded": len(found_rows), "raised": raised, "cleared": cleared, "errors": errors}


# ---- a temporary change past its end date (#755) -----------------------------------------------------------------

def _temporary_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("temporary_moc_overdue") or exception_type(
        "temporary_moc_overdue", "Temporary MOC overdue", role="role:engineer", audience=("ioc",), joins=False,
        description="A temporary management of change still open past the date it was approved to end. Restore the "
                    "original, make the change permanent under its own MOC, or extend it with its risk reviewed. It "
                    "clears when the change is closed or its end date moves out.")


TEMPORARY_TYPE = _temporary_type().name


@detectors.register("temporary_moc_overdue", title="A temporary MOC still open past its end date",
                    kind=detectors.RULE, cadence="daily", raises=(TEMPORARY_TYPE,),
                    needs={"temporary change records": lambda conn, pack: conn.execute(
                        "select 1 from golden g join entity c on c.fuzer_id = g.fuzer_id and c.kind = 'change' "
                        "where g.field = 'change_class' limit 1").fetchone() is not None},
                    eval="test:tests/evals/test_safeguards_kestrel.py::"
                         "test_a_temporary_moc_past_its_end_date_is_work_until_it_closes",
                    found=lambda out: len(out["raised"]))
def watch_temporary(conn, by: str = "agent:safeguards", now: datetime | None = None) -> dict:
    """Each temporary change record open past its end date raised as `temporary_moc_overdue` for the engineer, with
    its owner on the finding (keyed by the change); open ones closed or extended since are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    now = clock.aware(now) if now else datetime.now(UTC)
    held = [r["key"] for r in conn.execute(
        "select key from exception_record where type = %s and state = 'open'", (TEMPORARY_TYPE,))]
    late = [(c, st) for c in changes(conn) if (st := stage(c, now))["expired"]]
    raised, cleared, errors = [], [], []
    for c, st in late:
        on = ", ".join(c["about_labels"]) or "the plant"
        found = Finding(
            "safeguards.temporary_moc_overdue", DETERMINISTIC, method="Change records read against each temporary "
            "change's end date", start=c["closes"], data={"change": c["label"], "overdue_days": st["overdue_days"]},
            items=(fact("Change", f"{c['label']}: {c['title']}"), fact("Type", c["change_type"]),
                   fact("State", c["state"]), fact("About", on), at("Opened", c["opened"]), at("Should end", c["closes"]),
                   fact("Overdue", st["overdue_days"], "days"), fact("Owner", c["owner"] or "none on file"),
                   fact("Work orders", ", ".join(c["work_orders"]) or "none on file")))
        try:
            x = raise_exception(conn, TEMPORARY_TYPE, c["label"], f"Temporary MOC {c['label']} on {on} still open, "
                                f"{st['overdue_days']:g} days past its end date", by, source="safeguards",
                                refs=[c["id"], *c["about"]], priority=0.7, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{c['label']}: {e}")
            continue
        raised.append({"change": c["label"], "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
    for key in set(held) - {c["label"] for c, _ in late}:
        clear_exception(conn, TEMPORARY_TYPE, key, by, reason="the change is closed or no longer past its end date")
        cleared.append(key)
    conn.commit()
    return {"overdue": len(late), "raised": raised, "cleared": cleared, "errors": errors}
