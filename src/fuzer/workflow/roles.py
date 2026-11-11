"""Roles, routing and escalation as data: who does a step, how long they have, and where it goes next.

- A role (work_role) is a person role or an agent role. Its default SLA applies to person steps that set none;
  its escalate_to is where an overdue step goes when the step names nowhere itself; its assignment says whether
  steps wait in the queue for someone to claim them, or go to a member round-robin or to the least loaded.
- A person can be away (until a time) with a delegate: steps assigned to them go to the delegate, auto-assignment
  skips them, and the delegate's inbox shows what was theirs.
- Workload, statistics per process and the running instances of a process, for the Workflows pages.

The built-in roles are seeded the first time; after that the table is the truth (the BPMN checks read it).
"""

from datetime import datetime, timedelta

from . import bpmn

BUILTIN = {
    "engineer": ("person", "Process and facilities engineers: triage upsets, confirm fixes", "P1D", "ops_lead"),
    "ops_lead": ("person", "Operations lead: approves actions with an effect outside Fuzer, reviews daily reports",
                 "PT8H", None),
    "production_engineer": ("person", "Production engineering: wells, gas lift, deferment", "P1D", "ops_lead"),
    "data_steward": ("person", "Master data steward: source problems and register gaps", "P1D", "ops_lead"),
    "maintenance_planner": ("person", "Plans maintenance: raises and schedules SAP notifications", "P2D", "ops_lead"),
    "operator": ("person", "Control room and field operators", "PT4H", "ops_lead"),
    "workflow_admin": ("person", "Designs and publishes processes, manages roles", None, None),
    "sre": ("agent", "Agentic SRE: detects and groups upsets", None, None),
    "production-surveillance": ("agent", "Production trends, deferment, constraints and gas lift", None, None),
    "wells-subsurface": ("agent", "Well history, tests and intervention candidates", None, None),
    "field-operations-facilities": ("agent", "Diagnoses upsets from the graph and process data", None, None),
    "maintenance-integrity": ("agent", "Maintenance history, integrity, drafts notifications", None, None),
    "work-management-supply-chain": ("agent", "Work backlog, job readiness and spares", None, None),
    "hse-incident-management": ("agent", "Incident evidence for accountable HSE review", None, None),
    "asset-performance-reporting": ("agent", "Cross-domain summaries", None, None),
    # Retired names, still served so steps already in flight on published definitions finish (agents/worker.py).
    "troubleshooter": ("agent", "Retired: now field-operations-facilities", None, None),
    "production": ("agent", "Retired: now production-surveillance", None, None),
    "maintenance": ("agent", "Retired: now maintenance-integrity", None, None),
    "reporter": ("agent", "Drafts the daily operations report", None, None),
}
ASSIGNMENTS = ("queue", "round_robin", "least_loaded")
MAX_HOPS = 3


class RoleError(ValueError):
    pass


def seed(conn) -> int:
    n = 0
    for name, (kind, desc, sla, esc) in BUILTIN.items():
        n += conn.execute("insert into work_role (name, kind, description, default_sla, escalate_to, updated_by) "
                          "values (%s, %s, %s, %s, %s, 'seed') on conflict do nothing",
                          (name, kind, desc, sla, esc)).rowcount
    return n


_seen: tuple | None = None


def refresh(conn) -> None:
    """Load the roles into the sets the BPMN parser and checks use (in place, so their defaults see them)."""
    global _seen
    try:
        rows = conn.execute("select name, kind, default_sla from work_role").fetchall()
    except Exception:
        conn.rollback()
        return
    if not rows:
        seed(conn)
        rows = conn.execute("select name, kind, default_sla from work_role").fetchall()
    key = tuple(sorted((r["name"], r["kind"], r["default_sla"] or "") for r in rows))
    if key == _seen:
        return
    _seen = key
    bpmn.PERSON_ROLES.clear()
    bpmn.PERSON_ROLES.update(r["name"] for r in rows if r["kind"] == "person")
    bpmn.AGENT_ROLES.clear()
    bpmn.AGENT_ROLES.update(r["name"] for r in rows if r["kind"] == "agent")
    bpmn.ROLE_SLA.clear()
    bpmn.ROLE_SLA.update({r["name"]: r["default_sla"] for r in rows if r["default_sla"]})
    from . import engine

    engine._parsed.clear()  # lanes are read as person or agent roles when a definition is parsed


def role(conn, name: str | None) -> dict | None:
    if not name:
        return None
    r = conn.execute("select * from work_role where name = %s", (name,)).fetchone()
    return dict(r) if r else None


def listing(conn, now: datetime) -> list[dict]:
    """Every role with its members (and who's away), the published processes that use it, and its workload."""
    from . import engine

    refresh(conn)
    used: dict[str, set[str]] = {}
    for r in conn.execute("select id, process_key from work_definition where state = 'published'").fetchall():
        p = engine._definition(conn, r["id"])
        for n in p.nodes.values():
            for role_name in (n.role, n.agent, n.attrs.get("escalateTo")):
                if role_name:
                    used.setdefault(role_name, set()).add(r["process_key"])
    people = engine.people(conn)
    load = _load(conn, now)
    out = []
    for r in conn.execute("select * from work_role order by kind desc, name"):
        members = [{"name": p["name"], "away": bool(p["away_until"] and p["away_until"] > now),
                    "away_until": p["away_until"], "delegate": p["delegate"]}
                   for p in people if r["name"] in (p["roles"] or [])]
        w = load["roles"].get(r["name"], {})
        out.append({**dict(r), "members": members, "processes": sorted(used.get(r["name"], ())),
                    "waiting": w.get("waiting", 0), "overdue": w.get("overdue", 0), "escalated": w.get("escalated", 0),
                    "uncovered": r["kind"] == "person" and bool(used.get(r["name"])) and
                    not any(not m["away"] for m in members)})
    return out


def set_role(conn, name: str, by: str, **values) -> dict:
    """Add or change a role (kind, description, default_sla, escalate_to, assignment)."""
    from . import engine

    name = name.strip().lower().replace(" ", "_")
    if not name or not name.replace("_", "").isalnum():
        raise RoleError("a role name is letters, digits and _")
    allowed = {"kind", "description", "default_sla", "escalate_to", "assignment"}
    if set(values) - allowed:
        raise RoleError(f"a role has {sorted(allowed)}")
    old = role(conn, name)
    if old is None and values.get("kind") not in ("person", "agent"):
        raise RoleError("a new role needs a kind: person or agent")
    if values.get("default_sla") and bpmn.parse_duration(values["default_sla"]) is None:
        raise RoleError(f"default_sla {values['default_sla']!r} isn't an ISO 8601 duration (PT4H, P1D)")
    if values.get("assignment") and values["assignment"] not in ASSIGNMENTS:
        raise RoleError(f"assignment is one of {list(ASSIGNMENTS)}")
    if (esc := values.get("escalate_to")):
        target = role(conn, esc)
        if not target or target["kind"] != "person":
            raise RoleError(f"escalate_to must be a person role, not {esc!r}")
        if esc == name:
            raise RoleError("a role can't escalate to itself")
    merged = {**(old or {}), **values}
    conn.execute(
        """
        insert into work_role (name, kind, description, default_sla, escalate_to, assignment, updated_by, updated_at)
        values (%s, %s, %s, %s, %s, %s, %s, now())
        on conflict (name) do update set kind = excluded.kind, description = excluded.description,
            default_sla = excluded.default_sla, escalate_to = excluded.escalate_to, assignment = excluded.assignment,
            updated_by = excluded.updated_by, updated_at = now()
        """, (name, merged["kind"], merged.get("description"), merged.get("default_sla") or None,
              merged.get("escalate_to") or None, merged.get("assignment") or "queue", by))
    engine._event(conn, "role_set", by, data={"role": name, "old": {k: (old or {}).get(k) for k in values}, "new": values})
    refresh(conn)
    return role(conn, name)


def delete_role(conn, name: str, by: str) -> dict:
    from . import engine

    r = role(conn, name)
    if not r:
        raise RoleError(f"no role {name!r}")
    refresh(conn)
    users = [x["process_key"] for x in conn.execute("select distinct process_key, id from work_definition "
                                                     "where state = 'published'")
             if any(name in (n.role, n.agent, n.attrs.get("escalateTo"))
                    for n in engine._definition(conn, x["id"]).nodes.values())]
    held = [p["name"] for p in conn.execute("select name from work_person where %s = any(roles) union "
                                            "select name from fuzer.work_person_cover where %s = any(roles)",
                                            (name, name))]
    waiting = conn.execute("select count(*) as n from work_token where state = 'waiting' and role = %s", (name,)).fetchone()["n"]
    esc = [x["name"] for x in conn.execute("select name from work_role where escalate_to = %s", (name,))]
    if users or held or waiting or esc:
        raise RoleError(f"{name} is in use: " + "; ".join(x for x in (
            f"processes {', '.join(sorted(set(users)))}" if users else "", f"held by {', '.join(held)}" if held else "",
            f"{waiting} waiting steps" if waiting else "", f"escalated to from {', '.join(esc)}" if esc else "") if x))
    conn.execute("delete from work_role where name = %s", (name,))
    engine._event(conn, "role_deleted", by, data={"role": name})
    refresh(conn)
    return {"deleted": name}


# ---- people: away and delegates ------------------------------------------------------------------

def _on_roster(conn, person: str) -> bool:
    """Whether `person` works this project: on its roster, or overarching and covering it (#497). An overarching
    person gets a roster row with no roles of its own, so being away here is recorded here."""
    from .definitions import _covers

    if conn.execute("select 1 from work_person where name = %s", (person,)).fetchone():
        return True
    if _covers(conn, person):
        conn.execute("insert into work_person (name, roles) values (%s, '{}') on conflict do nothing", (person,))
        return True
    return False


def set_away(conn, person: str, by: str, until: datetime | None, delegate: str | None = None,
             note: str | None = None) -> dict:
    """Mark a person away until a time (None: back), with who covers for them."""
    from . import engine

    if not _on_roster(conn, person):
        raise RoleError(f"no person {person!r}")
    if delegate:
        if delegate == person:
            raise RoleError("a person can't cover for themselves")
        if not _on_roster(conn, delegate):
            raise RoleError(f"no person {delegate!r} to cover")
    conn.execute("update work_person set away_until = %s, delegate = %s, note = %s where name = %s",
                 (until, delegate if until else None, note if until else None, person))
    moved = []
    if until and until > engine.utcnow():  # what they hold now goes to whoever covers, else back to its queue
        cover = covering(conn, delegate, engine.utcnow()) if delegate else None
        for t in conn.execute("update work_token set assignee = %s, claimed_by = null, lease_until = null "
                              "where state = 'waiting' and kind in ('user', 'fix') and assignee = %s returning id, item_id, process_id",
                              (cover, person)).fetchall():
            moved.append(t["id"])
            engine._event(conn, "delegated" if cover else "released", by, t["item_id"], t["process_id"], t["id"],
                          data={"for": person, "to": cover, "reason": f"{person} is away"})
            engine._sync_item(conn, t["item_id"], engine.utcnow())
    engine._event(conn, "away_set", by, data={"person": person, "until": until.isoformat() if until else None,
                                              "delegate": delegate, "moved": moved})
    return dict(conn.execute("select * from work_person where name = %s", (person,)).fetchone())


def covering(conn, person: str | None, now: datetime) -> str | None:
    """Who a step for `person` goes to: their delegate while they're away (following a chain of delegates), nobody (the
    role's queue) when they're away with no one covering, else them."""
    seen = set()
    while person and person not in seen:
        seen.add(person)
        r = conn.execute("select away_until, delegate from work_person where name = %s", (person,)).fetchone()
        if not r or not (r["away_until"] and r["away_until"] > now):
            return person
        if not r["delegate"]:
            return None
        person = r["delegate"]
    return person


def covered_by(conn, person: str, now: datetime) -> list[str]:
    """The people `person` is covering for right now (their steps show in person's inbox)."""
    return [r["name"] for r in conn.execute(
        "select name from work_person where delegate = %s and away_until > %s", (person, now))]


def pick(conn, role_name: str | None, now: datetime) -> str | None:
    """A member for a new step when its role assigns automatically (round_robin, least_loaded); None: the queue."""
    r = role(conn, role_name)
    if not r or r["assignment"] == "queue" or r["kind"] != "person":
        return None
    from .definitions import people

    members = [p["name"] for p in people(conn)
               if role_name in p["roles"] and (p["away_until"] is None or p["away_until"] <= now)]
    if not members:
        return None
    if r["assignment"] == "least_loaded":
        load = {x["assignee"]: x["n"] for x in conn.execute(
            "select assignee, count(*) as n from work_token where state = 'waiting' and kind in ('user', 'fix') "
            "and assignee = any(%s) group by 1", (members,))}
        chosen = min(members, key=lambda m: (load.get(m, 0), m))
    else:
        after = r["last_assigned"]
        chosen = next((m for m in members if after is None or m > after), members[0])
    conn.execute("update work_role set last_assigned = %s where name = %s", (chosen, role_name))
    return chosen


def escalation_target(conn, step_role: str | None, step_escalate_to: str | None) -> str | None:
    if step_escalate_to:
        return step_escalate_to
    r = role(conn, step_role)
    return r["escalate_to"] if r else None


def sla_of(conn, role_name: str | None) -> timedelta | None:
    r = role(conn, role_name)
    return bpmn.parse_duration(r["default_sla"]) if r and r["default_sla"] else None


# ---- workload, statistics, instances ---------------------------------------------------------------

def _load(conn, now: datetime) -> dict:
    roles_, people = {}, {}
    for r in conn.execute(
        """
        select role, assignee, count(*) as waiting, count(*) filter (where due_at < %(now)s) as overdue,
               count(*) filter (where escalated_at is not null) as escalated
        from work_token where state = 'waiting' and kind in ('user', 'fix') group by 1, 2
        """, {"now": now}):
        if r["assignee"]:
            p = people.setdefault(r["assignee"], {"waiting": 0, "overdue": 0, "escalated": 0})
        else:
            p = roles_.setdefault(r["role"], {"waiting": 0, "overdue": 0, "escalated": 0})
        for k in ("waiting", "overdue", "escalated"):
            p[k] += r[k]
    return {"roles": roles_, "people": people}


def workload(conn, now: datetime) -> dict:
    """Per person: steps assigned, overdue, away and who covers; per role: unclaimed, overdue, escalated."""
    load = _load(conn, now)
    from . import definitions

    people = []
    for p in definitions.people(conn):
        w = load["people"].get(p["name"], {})
        people.append({"name": p["name"], "roles": p["roles"], "assigned_roles": p["assigned_roles"], "owner": p["owner"],
                       "display_name": p["display_name"], "account_enabled": p["account_enabled"],
                       "assigned": w.get("waiting", 0),
                       "overdue": w.get("overdue", 0), "away": bool(p["away_until"] and p["away_until"] > now),
                       "away_until": p["away_until"], "delegate": p["delegate"], "note": p["note"],
                       "covers": p.get("covers"), "cover_roles": p.get("cover_roles", [])})
    agents = [{"role": r["role"], "waiting": r["waiting"], "failing": r["failing"]} for r in conn.execute(
        "select role, count(*) as waiting, count(*) filter (where attempts > 0) as failing from work_token "
        "where state = 'waiting' and kind = 'agent' group by 1 order by 1")]
    failures = [dict(r) for r in conn.execute(
        "select e.at, e.actor, e.item_id, e.data, i.title from work_event e left join work_item i on i.id = e.item_id "
        "where e.kind in ('agent_failed', 'agent_gave_up') order by e.id desc limit 20")]
    return {"people": people, "roles": load["roles"], "agents": agents, "agent_failures": failures}


def process_stats(conn, now: datetime, days: int = 30) -> list[dict]:
    """Per published process: running, steps waiting and overdue, finished in the last `days`, median and 90th
    percentile cycle time (hours), and the share of person steps that went past their SLA."""
    since = now - timedelta(days=days)
    rows = conn.execute(
        """
        with p as (select process_key,
                          count(*) filter (where state = 'running') as running,
                          count(*) filter (where ended_at >= %(since)s) as finished,
                          percentile_cont(0.5) within group (order by extract(epoch from ended_at - started_at) / 3600)
                              filter (where ended_at >= %(since)s) as median_h,
                          percentile_cont(0.9) within group (order by extract(epoch from ended_at - started_at) / 3600)
                              filter (where ended_at >= %(since)s) as p90_h
                   from work_process group by 1),
             t as (select p.process_key,
                          count(*) filter (where t.state = 'waiting' and t.kind in ('user', 'fix')) as waiting,
                          count(*) filter (where t.state = 'waiting' and t.kind in ('user', 'fix') and t.due_at < %(now)s) as overdue,
                          count(*) filter (where t.kind = 'user' and t.created_at >= %(since)s) as steps,
                          count(*) filter (where t.kind = 'user' and t.created_at >= %(since)s and t.escalated_at is not null) as breached
                   from work_token t join work_process p on p.id = t.process_id group by 1)
        select d.process_key, d.name, d.version, coalesce(p.running, 0) as running, coalesce(p.finished, 0) as finished,
               p.median_h, p.p90_h, coalesce(t.waiting, 0) as waiting, coalesce(t.overdue, 0) as overdue,
               coalesce(t.steps, 0) as steps, coalesce(t.breached, 0) as breached
        from work_definition d left join p on p.process_key = d.process_key left join t on t.process_key = d.process_key
        where d.state = 'published' order by d.process_key
        """, {"now": now, "since": since}).fetchall()
    return [{**dict(r), "sla_breach_pct": round(100 * r["breached"] / r["steps"], 1) if r["steps"] else None,
             "median_h": round(r["median_h"], 1) if r["median_h"] is not None else None,
             "p90_h": round(r["p90_h"], 1) if r["p90_h"] is not None else None} for r in rows]


def instances(conn, process_key: str, now: datetime, state: str | None = "running", limit: int = 200) -> list[dict]:
    """A process's instances: their case, open steps (who has them, due, overdue, escalated), age and version."""
    return [dict(r) for r in conn.execute(
        """
        select p.id, p.item_id, i.title, i.priority, p.state, p.outcome, p.started_at, p.ended_at, p.started_by,
               d.version, extract(epoch from coalesce(p.ended_at, %(now)s) - p.started_at) / 3600 as age_h,
               coalesce(json_agg(json_build_object('task_id', t.id, 'step', coalesce(t.name, t.element), 'kind', t.kind,
                   'role', t.role, 'assignee', t.assignee, 'due_at', t.due_at, 'overdue', t.due_at < %(now)s,
                   'escalated', t.escalated_at is not null) order by t.id)
                   filter (where t.id is not null), '[]') as open_steps
        from work_process p join work_item i on i.id = p.item_id join work_definition d on d.id = p.definition_id
        left join work_token t on t.process_id = p.id and t.state = 'waiting' and t.kind in ('user', 'fix', 'agent')
        where p.process_key = %(key)s and (%(state)s::text is null or p.state = %(state)s)
        group by p.id, i.id, d.version order by p.started_at desc limit %(limit)s
        """, {"key": process_key, "state": state, "now": now, "limit": limit})]


def routing(conn, definition_id: int) -> list[dict]:
    """Each step of a process version as it will route: who does it (role, or agent), its SLA (the step's, else its
    role's default), where it goes when that passes (the step's escalateTo, else the role chain), how the role
    assigns, the form, and whether it needs a person."""
    from . import engine

    refresh(conn)
    p = engine._definition(conn, definition_id)
    rows = {r["name"]: dict(r) for r in conn.execute("select * from work_role")}
    out = []
    for n in p.nodes.values():
        if n.type not in ("userTask", "serviceTask"):
            continue
        r = rows.get(n.role or n.agent or "") or {}
        chain, seen, cur = [], set(), n.attrs.get("escalateTo") or r.get("escalate_to")
        while cur and cur not in seen and len(chain) < MAX_HOPS:
            chain.append(cur)
            seen.add(cur)
            cur = (rows.get(cur) or {}).get("escalate_to")
        out.append({"element": n.id, "step": n.label, "kind": "person" if n.type == "userTask" else "agent",
                    "role": n.role or n.agent, "sla": n.attrs.get("sla") or r.get("default_sla"),
                    "sla_from": "step" if n.attrs.get("sla") else ("role" if r.get("default_sla") else None),
                    "escalation": chain, "escalation_from": "step" if n.attrs.get("escalateTo") else ("role" if chain else None),
                    "assignment": r.get("assignment"), "form": n.attrs.get("form"), "requires_human": n.requires_human,
                    "fallback_role": n.attrs.get("fallbackRole")})
    return out
