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
"""

from datetime import UTC, datetime

from ..core import detectors
from ..core.findings import DETERMINISTIC, Finding, at, fact

IN_PLACE = ("active", "in place", "applied", "bypassed", "inhibited", "overridden", "forced")


def _when(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


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


def reach(conn, subjects: list[str], system: bool = False) -> list[str]:
    """The subjects, what is installed at or part of them and where they are installed, and with `system` the rest of
    each one's train (and what drives it) and what is installed on or part of that."""
    near = _around(conn, list(subjects or []))
    if system and near:
        from ..graph import structure

        plant = structure.load(conn)
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
             in_place: bool = True) -> list[dict]:
    """The bypasses in place at `at_time` (default now), each with its safeguard, what that protects and acts on,
    whether it is overdue and by how long; on `subjects` (see `reach`) when given. `in_place=False` returns every
    bypass on file."""
    now = at_time or datetime.now(UTC)
    near = set(reach(conn, subjects, system)) if subjects is not None else None
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
               (select coalesce(array_agg(e.dst order by e.dst), '{}') from edge e
                where e.src = c.fuzer_id and e.rel = 'concerns' and e.valid_to is null) as about
        from entity c
        left join entity_label cl on cl.fuzer_id = c.fuzer_id
        left join golden g on g.fuzer_id = c.fuzer_id
        where c.kind = 'change' and c.retired_at is null
          and (%(near)s::text[] is null or exists (select 1 from edge e where e.src = c.fuzer_id
               and e.rel = 'concerns' and e.valid_to is null and e.dst = any(%(near)s)))
        group by c.fuzer_id, cl.label order by cl.label
        """, {"near": near}).fetchall()
    return [{**dict(r), "about": list(r["about"]), "about_labels": _names(conn, list(r["about"]))} for r in rows
            if not open_only or (r["state"] or "").lower() not in CLOSED_CHANGE]


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

